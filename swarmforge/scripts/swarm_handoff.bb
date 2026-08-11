#!/usr/bin/env bb

(ns swarm-handoff
  (:require [babashka.fs :as fs]
            [babashka.process :as process]
            [clojure.java.shell :refer [sh]]
            [clojure.string :as str]))

(def script-dir (fs/parent *file*))
(def telemetry-script (fs/path (fs/parent script-dir) "telemetry" "metrics.py"))
(def route-validator (fs/path (fs/parent script-dir) "gates" "route_recommendation.py"))

(def usage-text
  (str "Usage: swarm_handoff.sh <draft-file>\n\n"
       "Draft formats:\n\n"
       "type: git_handoff\n"
       "to: <role>[,<role>...]\n"
       "priority: NN\n"
       "task: <short-stable-task-name>\n"
       "commit: <10-char-commit-abbrev>\n\n"
       "type: task_handoff\n"
       "to: <role>[,<role>...]\n"
       "priority: NN\n"
       "task: <short-stable-task-name>\n"
       "base_commit: <10-char-commit-abbrev>\n\n"
       "## Objective\n...\n\n## Acceptance criteria\n...\n\n"
       "## Constraints\n...\n\n## Context\n...\n\n"
       "type: result_handoff\n"
       "to: <role>[,<role>...]\n"
       "priority: NN\n"
       "task: <short-stable-task-name>\n"
       "outcome: <changed|reviewed>\n"
       "base_commit: <10-char-commit-abbrev>\n"
       "commit: <10-char-commit-abbrev>\n\n"
       "## Summary\n...\n\n## Checks\n...\n\n## Unresolved risks\n...\n\n"
       "type: note\n"
       "to: <role>[,<role>...]\n"
       "priority: NN\n"
       "message: <one line, max 80 chars>"))

(def reserved-fields #{"id" "from" "role" "recipient" "route" "created_at" "enqueued_at" "dequeued_at" "completed_at"})
(def allowed-fields #{"type" "to" "priority" "task" "commit" "base_commit" "outcome" "message"})
(def allowed-types #{"git_handoff" "task_handoff" "result_handoff" "note"})
(def max-payload-bytes 8192)
(def payload-sections
  {"task_handoff" ["Objective" "Acceptance criteria" "Constraints" "Context"]
   "result_handoff" ["Summary" "Checks" "Unresolved risks"]})

(declare project-root)

(defn usage []
  (binding [*out* *err*]
    (println usage-text)))

(defn exit! [status message]
  (binding [*out* *err*]
    (when message
      (println message)))
  (System/exit status))

(defn command
  ([dir & args]
   (let [result (apply sh (concat args [:dir (str dir)]))]
     result)))

(defn record-telemetry! [& args]
  (try
    (apply process/sh
           (concat [{:continue true}]
                   ["python3" (str telemetry-script) "record"
                    "--root" (str (project-root))]
                   args))
    (catch Exception _ nil)))

(defn git-root []
  (let [result (command "." "git" "rev-parse" "--show-toplevel")]
    (when (zero? (:exit result))
      (str/trim (:out result)))))

(defn git-common-dir []
  (let [result (command "." "git" "rev-parse" "--git-common-dir")]
    (when (zero? (:exit result))
      (let [path (str/trim (:out result))]
        (if (fs/absolute? path)
          (str (fs/path path))
          (str (fs/absolutize path)))))))

(defn project-root []
  (if-let [root (git-root)]
    (if (fs/exists? (fs/path root ".swarmforge" "roles.tsv"))
      root
      (if-let [common (git-common-dir)]
        (let [candidate (str (fs/parent common))]
          (if (fs/exists? (fs/path candidate ".swarmforge" "roles.tsv"))
            candidate
            (exit! 1 "Cannot find SwarmForge project root")))
        (exit! 1 "Cannot find SwarmForge project root")))
    (exit! 1 "Cannot find SwarmForge project root")))

(defn roles-file []
  (fs/path (project-root) ".swarmforge" "roles.tsv"))

(defn role-known? [role]
  (some (fn [line]
          (= role (first (str/split line #"\t"))))
        (str/split-lines (slurp (str (roles-file))))))

(defn sender-role []
  (if-let [role (not-empty (System/getenv "SWARMFORGE_ROLE"))]
    role
    (exit! 1 "Set SWARMFORGE_ROLE.")))

(defn state-dir []
  (fs/path (System/getProperty "user.dir") ".swarmforge" "handoffs"))

(defn timestamp []
  (.format java.time.format.DateTimeFormatter/ISO_INSTANT
           (java.time.Instant/now)))

(defn id-timestamp []
  (.format (java.time.format.DateTimeFormatter/ofPattern "yyyyMMdd'T'HHmmss'Z'")
           (.atZone (java.time.Instant/now) java.time.ZoneOffset/UTC)))

(defn valid-priority? [priority]
  (boolean (re-matches #"[0-9][0-9]" priority)))

(defn parse-draft [draft]
  (let [[header payload] (str/split (slurp (str draft)) #"\n\n" 2)]
    (loop [lines (str/split-lines header)
         line-no 0
         headers {}
         ordered []
         errors []]
    (if-let [line (first lines)]
      (let [line-no (inc line-no)]
        (cond
          (str/blank? line)
          (recur (next lines) line-no headers ordered errors)

          (not (str/includes? line ": "))
          (recur (next lines) line-no headers ordered
                 (conj errors (format "Line %d: expected 'field: value'." line-no)))

          :else
          (let [[field value] (str/split line #": " 2)]
            (cond
              (or (str/blank? field) (str/blank? value))
              (recur (next lines) line-no headers ordered
                     (conj errors (format "Line %d: field and value must both be non-empty." line-no)))

              (reserved-fields field)
              (recur (next lines) line-no headers ordered
                     (conj errors (format "Line %d: header '%s' is reserved and must not be written by agents." line-no field)))

              (not (allowed-fields field))
              (recur (next lines) line-no headers ordered
                     (conj errors (format "Line %d: unknown header '%s'." line-no field)))

              (contains? headers field)
              (recur (next lines) line-no headers ordered
                     (conj errors (format "Line %d: duplicate header '%s'." line-no field)))

              :else
              (recur (next lines) line-no (assoc headers field value) (conj ordered field) errors)))))
      {:headers headers :ordered ordered :payload (or payload "") :errors errors}))))

(defn parse-payload-sections [payload]
  (loop [lines (str/split-lines payload)
         current nil
         sections {}
         duplicates #{}]
    (if-let [line (first lines)]
      (if (str/starts-with? line "## ")
        (let [name (subs line 3)]
          (recur (next lines) name
                 (if (contains? sections name) sections (assoc sections name []))
                 (cond-> duplicates (contains? sections name) (conj name))))
        (recur (next lines) current
               (if current (update sections current conj line) sections)
               duplicates))
      {:sections (update-vals sections #(str/trim (str/join "\n" %)))
       :duplicates duplicates})))

(defn validate-payload [type payload]
  (let [required (get payload-sections type)
        byte-count (alength (.getBytes payload java.nio.charset.StandardCharsets/UTF_8))]
    (if required
      (let [{:keys [sections duplicates]} (parse-payload-sections payload)
            required-set (set required)]
        (vec
         (concat
          (when (> byte-count max-payload-bytes)
            [(format "Payload must be no larger than %d bytes; got %d."
                     max-payload-bytes byte-count)])
          (for [name required :when (not (contains? sections name))]
            (format "Missing payload section '## %s'." name))
          (for [name required :when (and (contains? sections name)
                                         (str/blank? (get sections name)))]
            (format "Payload section '## %s' must not be empty." name))
          (for [name (sort (remove required-set (keys sections)))]
            (format "Unknown payload section '## %s'." name))
          (for [name (sort duplicates)]
            (format "Duplicate payload section '## %s'." name)))))
      (cond-> []
        (not (str/blank? payload))
        (conj (format "Payload is not allowed for type '%s'." type))))))

(defn validate-recipients [to]
  (if (str/blank? to)
    [[] []]
    (let [recipients (str/split to #"," -1)]
      [recipients
       (loop [remaining recipients seen #{} errors []]
         (if-let [recipient (first remaining)]
           (let [errors (cond-> errors
                          (str/blank? recipient)
                          (conj "Header 'to' contains an empty recipient.")
                          (str/includes? recipient "_")
                          (conj (format "Recipient role '%s' is invalid; role names may not contain underscores." recipient))
                          (contains? seen recipient)
                          (conj (format "Duplicate recipient '%s'." recipient))
                          (and (not (str/blank? recipient)) (not (role-known? recipient)))
                          (conj (format "Unknown recipient role '%s'." recipient)))]
             (recur (next remaining) (conj seen recipient) errors))
           errors))])))

(defn canonical-commit [field commit]
  (let [matches (-> (command "." "git" "rev-parse" (str "--disambiguate=" commit))
                    :out
                    str/split-lines
                    vec)]
    (cond
      (not= 1 (count matches))
      [nil (format "Header '%s' must resolve to exactly one Git object; '%s' matched %d."
                   field commit (count matches))]

      :else
      (let [object (first matches)
            object-type (str/trim (:out (command "." "git" "cat-file" "-t" object)))]
        (if (= "commit" object-type)
          [(str/trim (:out (command "." "git" "rev-parse" "--short=10" object))) nil]
          [nil (format "Header '%s' must resolve to a commit; '%s' resolves to '%s'."
                       field commit object-type)])))))

(defn validate [headers ordered payload]
  (let [type (get headers "type")
        to (get headers "to")
        priority (get headers "priority")
        commit (get headers "commit")
        base-commit (get headers "base_commit")
        outcome (get headers "outcome")
        task-name (get headers "task")
        note-message (get headers "message")
        [recipients recipient-errors] (validate-recipients to)
        field-errors (for [field ordered
                           :let [valid? (case [type field]
                                          ["git_handoff" "type"] true
                                          ["git_handoff" "to"] true
                                          ["git_handoff" "priority"] true
                                          ["git_handoff" "task"] true
                                          ["git_handoff" "commit"] true
                                          ["task_handoff" "type"] true
                                          ["task_handoff" "to"] true
                                          ["task_handoff" "priority"] true
                                          ["task_handoff" "task"] true
                                          ["task_handoff" "base_commit"] true
                                          ["result_handoff" "type"] true
                                          ["result_handoff" "to"] true
                                          ["result_handoff" "priority"] true
                                          ["result_handoff" "task"] true
                                          ["result_handoff" "outcome"] true
                                          ["result_handoff" "base_commit"] true
                                          ["result_handoff" "commit"] true
                                          ["note" "type"] true
                                          ["note" "to"] true
                                          ["note" "priority"] true
                                          ["note" "message"] true
                                          false)]
                           :when (and type (not valid?))]
                       (format "Header '%s' is not allowed for type '%s'." field type))
        base-errors (cond-> []
                      (str/blank? type) (conj "Missing required header 'type'.")
                      (str/blank? to) (conj "Missing required header 'to'.")
                      (str/blank? priority) (conj "Missing required header 'priority'.")
                      (and (not (str/blank? type)) (not (allowed-types type)))
                      (conj (format "Header 'type' must be one of git_handoff, task_handoff, result_handoff, or note; got '%s'." type))
                      (and (not (str/blank? priority)) (not (valid-priority? priority)))
                      (conj (format "Header 'priority' must be two digits from 00 to 99; got '%s'." priority)))
        [canonical commit-error]
        (if (#{"git_handoff" "result_handoff"} type)
          (cond
            (str/blank? commit) [nil (format "Missing required header 'commit' for %s." type)]
            (not (re-matches #"[0-9a-fA-F]{10}" commit))
            [nil (format "Header 'commit' must be exactly 10 hexadecimal characters; got '%s'." commit)]
            :else (canonical-commit "commit" commit))
          [nil nil])
        [canonical-base base-commit-error]
        (if (#{"task_handoff" "result_handoff"} type)
          (cond
            (str/blank? base-commit)
            [nil (format "Missing required header 'base_commit' for %s." type)]
            (not (re-matches #"[0-9a-fA-F]{10}" base-commit))
            [nil (format "Header 'base_commit' must be exactly 10 hexadecimal characters; got '%s'." base-commit)]
            :else (canonical-commit "base_commit" base-commit))
          [nil nil])
        git-errors (cond-> []
                     (#{"git_handoff" "result_handoff"} type)
                     (into (cond-> []
                             (str/blank? task-name)
                             (conj (format "Missing required header 'task' for %s." type))
                             (> (count (or task-name "")) 80)
                             (conj (format "Header 'task' must be no longer than 80 characters; got %d." (count task-name)))))
                     (and (not (#{"git_handoff" "result_handoff"} type)) (not (str/blank? commit)))
                     (conj "Header 'commit' is only allowed for git_handoff or result_handoff.")
                     (and (not (#{"git_handoff" "task_handoff" "result_handoff"} type)) (not (str/blank? task-name)))
                     (conj "Header 'task' is only allowed for git_handoff, task_handoff, or result_handoff.")
                     commit-error
                     (conj commit-error))
        task-errors (cond-> []
                      (= "task_handoff" type)
                      (into (cond-> []
                              (str/blank? task-name)
                              (conj "Missing required header 'task' for task_handoff.")
                              (> (count (or task-name "")) 80)
                              (conj (format "Header 'task' must be no longer than 80 characters; got %d." (count task-name)))))
                      (and (not (#{"task_handoff" "result_handoff"} type))
                           (not (str/blank? base-commit)))
                      (conj "Header 'base_commit' is only allowed for task_handoff or result_handoff.")
                      base-commit-error
                      (conj base-commit-error))
        result-errors (cond-> []
                        (= "result_handoff" type)
                        (into (cond-> []
                                (str/blank? outcome)
                                (conj "Missing required header 'outcome' for result_handoff.")
                                (and (not (str/blank? outcome))
                                     (not (#{"changed" "reviewed"} outcome)))
                                (conj (format "Header 'outcome' must be changed or reviewed; got '%s'." outcome))))
                        (and (= "result_handoff" type)
                             canonical-base canonical
                             (not (zero? (:exit (command "." "git" "merge-base"
                                                        "--is-ancestor" canonical-base canonical)))))
                        (conj "Result commit must descend from base_commit.")
                        (and (= "result_handoff" type) (= "changed" outcome)
                             canonical-base canonical (= canonical-base canonical))
                        (conj "outcome: changed requires a commit after base_commit.")
                        (and (not= "result_handoff" type) (not (str/blank? outcome)))
                        (conj "Header 'outcome' is only allowed for result_handoff."))
        note-errors (cond-> []
                      (= "note" type)
                      (into (cond-> []
                              (str/blank? note-message)
                              (conj "Missing required header 'message' for note.")
                              (> (count (or note-message "")) 80)
                              (conj (format "Header 'message' must be no longer than 80 characters; got %d." (count note-message)))))
                      (and (not= "note" type) (not (str/blank? note-message)))
                      (conj "Header 'message' is only allowed for note."))]
    {:recipients recipients
     :canonical-commit canonical
     :canonical-base-commit canonical-base
     :errors (vec (concat base-errors recipient-errors field-errors git-errors task-errors
                          result-errors note-errors (validate-payload type payload)))}))

(defn next-sequence []
  (let [dir (state-dir)
        seq-file (fs/path dir "sequence")
        lock-dir (fs/path dir "sequence.lock")]
    (fs/create-dirs dir)
    (loop []
      (if (try
            (fs/create-dir lock-dir)
            true
            (catch java.nio.file.FileAlreadyExistsException _
              false))
        nil
        (do
          (Thread/sleep 50)
          (recur))))
    (try
      (let [last-value (if (fs/exists? seq-file)
                         (try
                           (Long/parseLong (str/trim (slurp (str seq-file))))
                           (catch Exception _ 0))
                         0)
            next-value (inc last-value)
            formatted (format "%06d" next-value)]
        (spit (str seq-file) (str formatted "\n"))
        formatted)
      (finally
        (fs/delete lock-dir)))))

(defn body [type sender canonical-commit outcome note-message payload]
  (case type
    "git_handoff" (str "Re-read your role and constitution.\n\nmerge_and_process " sender " " canonical-commit)
    "task_handoff" (str "Re-read your role and constitution.\n\n" payload)
    "result_handoff" (str "Re-read your role and constitution.\n\n"
                           (if (= "changed" outcome) "merge_and_process " "review_result ")
                           sender " " canonical-commit "\n\n" payload)
    "note" (str "Re-read your role and constitution.\n\n" note-message)))

(defn route-recommendation [commit]
  (let [root (System/getProperty "user.dir")
        artifact (fs/path root ".swarmforge" "artifacts" "route" "route.json")]
    (when (fs/regular-file? artifact)
      (let [result (process/sh {:continue true}
                               "python3" (str route-validator)
                               "--root" root
                               "--commit" commit
                               "--path" (str artifact))
            output (str/trim (:out result))]
        (when (and (zero? (:exit result)) (not (str/blank? output)))
          output)))))

(defn write-handoff! [{:keys [headers recipients canonical-commit canonical-base-commit
                              payload sender route]}]
  (let [timestamp-id (id-timestamp)
        created-at (timestamp)
        sequence (next-sequence)
        id (str timestamp-id "_" sequence "_from_" sender)
        recipient-slug (str/join "_" recipients)
        priority (get headers "priority")
        type (get headers "type")
        filename (str priority "_" timestamp-id "_" sequence "_from_" sender "_to_" recipient-slug ".handoff")
        outbox-dir (fs/path (state-dir) "outbox")
        tmp-dir (fs/path outbox-dir "tmp")
        tmp-file (fs/path tmp-dir (str filename ".tmp"))
        outbox-file (fs/path outbox-dir filename)
        handoff-body (body type sender canonical-commit (get headers "outcome")
                           (get headers "message") payload)
        lines (cond-> [(str "id: " id)
                       (str "from: " sender)
                       (str "to: " (str/join "," recipients))
                       (str "priority: " priority)
                       (str "type: " type)]
                (#{"git_handoff" "result_handoff"} type)
                (conj (str "role: " sender)
                      (str "task: " (get headers "task"))
                      (str "commit: " canonical-commit))
                (= "result_handoff" type)
                (conj (str "outcome: " (get headers "outcome")))
                (#{"task_handoff" "result_handoff"} type)
                (conj (str "base_commit: " canonical-base-commit))
                (= "task_handoff" type)
                (conj (str "task: " (get headers "task")))
                (= "note" type)
                (conj (str "message: " (get headers "message")))
                route
                (conj (str "route: " route))
                true
                (conj (str "created_at: " created-at)
                      ""
                      handoff-body))]
    (doseq [dir [tmp-dir outbox-dir (fs/path (state-dir) "sent") (fs/path (state-dir) "failed")]]
      (fs/create-dirs dir))
    (spit (str tmp-file) (str (str/join "\n" lines) "\n"))
    (fs/move tmp-file outbox-file)
    {:path outbox-file :id id}))

(defn error-report [draft errors]
  (binding [*out* *err*]
    (println "HANDOFF INVALID:" (str draft))
    (println)
    (println "Errors:")
    (doseq [error errors]
      (println "-" error))
    (println)
    (println usage-text)))

(defn run-lifecycle-hook! [event context]
  (let [args (mapcat identity context)
        result (apply process/sh
                      (concat [{:continue true}]
                              [(str (fs/path script-dir "lifecycle_hook.bb")) event]
                              args))]
    (when-not (zero? (:exit result))
      (binding [*out* *err*]
        (when-not (str/blank? (:err result))
          (print (:err result)))
        (when-not (str/blank? (:out result))
          (print (:out result)))
        (flush))
      (System/exit 2))))

(defn -main [& args]
  (when (not= 1 (count args))
    (usage)
    (System/exit 1))
  (let [draft (fs/path (first args))]
    (when-not (fs/regular-file? draft)
      (exit! 1 (str "Draft file not found: " draft)))
    (let [sender (sender-role)]
      (when-not (role-known? sender)
        (exit! 1 (str "Unknown sender role: " sender)))
      (let [{:keys [headers ordered payload errors]} (parse-draft draft)
            validation (validate headers ordered payload)
            all-errors (vec (concat errors (:errors validation)))]
        (when (seq all-errors)
          (error-report draft all-errors)
          (System/exit 2))
        (when (#{"git_handoff" "result_handoff"} (get headers "type"))
          (run-lifecycle-hook!
           "pre-handoff"
           {"SWARMFORGE_HOOK_DRAFT" (str draft)
            "SWARMFORGE_HOOK_TASK" (get headers "task")
            "SWARMFORGE_HOOK_COMMIT" (:canonical-commit validation)
            "SWARMFORGE_HOOK_RECIPIENTS" (str/join "," (:recipients validation))}))
        (let [route (when (#{"git_handoff" "result_handoff"} (get headers "type"))
                      (route-recommendation (:canonical-commit validation)))
              {:keys [path id]} (write-handoff! {:headers headers
                                                :recipients (:recipients validation)
                                                :canonical-commit (:canonical-commit validation)
                                                :canonical-base-commit (:canonical-base-commit validation)
                                                :payload payload
                                                :sender sender
                                                :route route})]
          (record-telemetry! "--event" "handoff_submitted"
                             "--id" id
                             "--result" "submitted"
                             "--handoff-count" (str (count (:recipients validation))))
          (fs/delete draft)
          (println "HANDOFF QUEUED:" (str path)))))))

(apply -main *command-line-args*)
