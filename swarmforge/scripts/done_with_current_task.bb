#!/usr/bin/env bb

(ns done-with-current-task
  (:require [babashka.fs :as fs]
            [babashka.process :as process]
            [clojure.string :as str]))

(def script-dir (fs/parent *file*))
(def telemetry-script (fs/path (fs/parent script-dir) "telemetry" "metrics.py"))

(defn inbox-dir []
  (fs/path (System/getProperty "user.dir") ".swarmforge" "handoffs" "inbox"))

(defn timestamp []
  (.format java.time.format.DateTimeFormatter/ISO_INSTANT
           (java.time.Instant/now)))

(defn handoff-files [dir]
  (if (fs/exists? dir)
    (->> (fs/list-dir dir)
         (filter #(and (fs/regular-file? %) (str/ends-with? (fs/file-name %) ".handoff")))
         (sort-by #(fs/file-name %))
         vec)
    []))

(defn header-field [file field]
  (let [prefix (str field ": ")]
    (some (fn [line]
            (when (str/starts-with? line prefix)
              (subs line (count prefix))))
          (take-while (complement str/blank?)
                      (str/split-lines (slurp (str file)))))))

(defn header-value [file field default]
  (or (header-field file field) default))

(defn batch-dirs [dir]
  (if (fs/exists? dir)
    (->> (fs/list-dir dir)
         (filter #(and (fs/directory? %) (str/starts-with? (fs/file-name %) "batch_")))
         (sort-by #(fs/file-name %))
         vec)
    []))

(defn set-header! [file field value]
  (let [lines (str/split-lines (slurp (str file)))
        prefix (str field ": ")
        tmp (fs/create-temp-file {:dir (fs/parent file) :prefix ".headers."})
        result (loop [remaining lines
                      out []
                      inserted? false
                      replaced? false]
                 (if-let [line (first remaining)]
                   (cond
                     (and (not inserted?) (str/blank? line))
                     (recur (next remaining)
                            (conj (cond-> out (not replaced?) (conj (str prefix value))) line)
                            true
                            replaced?)

                     (and (not inserted?) (str/starts-with? line prefix))
                     (recur (next remaining) (conj out (str prefix value)) inserted? true)

                     :else
                     (recur (next remaining) (conj out line) inserted? replaced?))
                   (cond-> out
                     (and (not inserted?) (not replaced?)) (conj (str prefix value)))))]
    (spit (str tmp) (str (str/join "\n" result) "\n"))
    (fs/move tmp file {:replace-existing true})))

(defn fail! [status & lines]
  (binding [*out* *err*]
    (doseq [line lines]
      (println line)))
  (System/exit status))

(defn duration-ms [dequeued-at completed-at]
  (when dequeued-at
    (try
      (.toMillis (java.time.Duration/between
                   (java.time.Instant/parse dequeued-at)
                   (java.time.Instant/parse completed-at)))
      (catch Exception _ nil))))

(defn record-telemetry! [& args]
  (try
    (apply process/sh
           (concat [{:continue true}]
                   ["python3" (str telemetry-script) "record"
                    "--root" (System/getProperty "user.dir")]
                   args))
    (catch Exception _ nil)))

(defn run-ready! []
  (process/exec (str (fs/path script-dir "ready_for_next_task.sh"))))

(defn -main []
  (let [inbox (inbox-dir)
        in-process-dir (fs/path inbox "in_process")
        completed-dir (fs/path inbox "completed")]
    (doseq [dir [in-process-dir completed-dir]]
      (fs/create-dirs dir))
    (let [in-process-batches (batch-dirs in-process-dir)
          in-process-files (handoff-files in-process-dir)]
      (when (seq in-process-batches)
        (fail! 2
               "CURRENT_WORK_IS_BATCH: use done_with_current.sh."
               (str/join "\n" (map #(str "- " %) in-process-batches))))
      (when (empty? in-process-files)
        (fail! 1 "NO_CURRENT_TASK"))
      (when (> (count in-process-files) 1)
        (fail! 2
               "AMBIGUOUS_TASK_STATE: multiple tasks are in process."
               (str/join "\n" (map #(str "- " %) in-process-files))))
      (let [source-file (first in-process-files)
            target-file (fs/path completed-dir (fs/file-name source-file))
            completed-at (timestamp)
            elapsed (duration-ms (header-field source-file "dequeued_at") completed-at)]
        (set-header! source-file "completed_at" completed-at)
        (when (fs/exists? target-file)
          (fail! 2 (str "AMBIGUOUS_TASK_STATE: completed file already exists: " target-file)))
        (fs/move source-file target-file)
        (apply record-telemetry!
               (cond-> ["--event" "task_completed"
                        "--id" (header-value target-file "task"
                                             (header-value target-file "id" "unknown"))
                        "--result" "completed"]
                 (some? elapsed) (conj "--duration-ms" (str elapsed))))
        (println "COMPLETED:" (str target-file))
        (run-ready!)))))

(-main)
