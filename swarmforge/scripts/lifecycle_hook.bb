#!/usr/bin/env bb

(ns lifecycle-hook
  (:require [babashka.fs :as fs]
            [clojure.java.shell :as sh]
            [clojure.string :as str])
  (:import [java.nio.charset StandardCharsets]
           [java.nio.file Files]
           [java.util UUID]
           [java.util.concurrent TimeUnit]))

(def default-max-bytes 8192)
(def default-timeout-seconds 180)

(defn exit! [status & lines]
  (binding [*out* *err*]
    (doseq [line lines]
      (when-not (str/blank? line)
        (println line))))
  (System/exit status))

(defn positive-long [value fallback]
  (if (and value (re-matches #"[1-9][0-9]*" value))
    (Long/parseLong value)
    fallback))

(defn git-path [& args]
  (let [result (apply sh/sh args)]
    (when (zero? (:exit result))
      (str/trim (:out result)))))

(defn project-root []
  (if-let [root (git-path "git" "rev-parse" "--show-toplevel")]
    (if (fs/exists? (fs/path root ".swarmforge" "roles.tsv"))
      (fs/path root)
      (if-let [common (git-path "git" "rev-parse" "--git-common-dir")]
        (let [common-path (fs/path common)
              absolute-common (if (fs/absolute? common-path)
                                common-path
                                (fs/absolutize common-path))
              candidate (fs/parent absolute-common)]
          (if (fs/exists? (fs/path candidate ".swarmforge" "roles.tsv"))
            candidate
            (exit! 1 "Cannot find SwarmForge project root")))
        (exit! 1 "Cannot find SwarmForge project root")))
    (exit! 1 "Cannot find SwarmForge project root")))

(defn context-map [args]
  (when (odd? (count args))
    (exit! 2 "Lifecycle hook context must contain key/value pairs."))
  (into {}
        (for [[key value] (partition 2 args)]
          (do
            (when-not (re-matches #"SWARMFORGE_HOOK_[A-Z0-9_]+" key)
              (exit! 2 (str "Invalid lifecycle hook context key: " key)))
            [key value]))))

(defn read-prefix [file max-bytes]
  (let [size (Files/size (fs/path file))
        amount (int (min size max-bytes))
        bytes (byte-array amount)]
    (with-open [stream (Files/newInputStream (fs/path file) (make-array java.nio.file.OpenOption 0))]
      (loop [offset 0]
        (when (< offset amount)
          (let [read (.read stream bytes offset (- amount offset))]
            (when (pos? read)
              (recur (+ offset read)))))))
    {:text (String. bytes StandardCharsets/UTF_8)
     :size size}))

(defn run-hook! [event context]
  (when-not (re-matches #"[a-z][a-z0-9-]*" event)
    (exit! 2 (str "Invalid lifecycle hook event: " event)))
  (let [root (project-root)
        hook (fs/path root "swarmforge" "hooks" event)]
    (when (fs/exists? hook)
      (when-not (and (fs/regular-file? hook) (fs/executable? hook))
        (exit! 2 (str "HOOK_INVALID " event ": expected executable file " hook)))
      (let [max-bytes (positive-long (System/getenv "SWARMFORGE_HOOK_MAX_BYTES") default-max-bytes)
            timeout-seconds (positive-long (System/getenv "SWARMFORGE_HOOK_TIMEOUT_SECONDS") default-timeout-seconds)
            artifact-dir (fs/path root ".swarmforge" "artifacts" "hooks")
            log-file (fs/path artifact-dir (str event "-" (UUID/randomUUID) ".log"))
            builder (ProcessBuilder. ^java.util.List [(str hook)])]
        (fs/create-dirs artifact-dir)
        (.directory builder (.toFile root))
        (.redirectErrorStream builder true)
        (.redirectOutput builder (.toFile log-file))
        (let [environment (.environment builder)]
          (.put environment "SWARMFORGE_HOOK_EVENT" event)
          (.put environment "SWARMFORGE_PROJECT_ROOT" (str root))
          (doseq [[key value] context]
            (.put environment key value)))
        (let [process (.start builder)
              finished? (.waitFor process timeout-seconds TimeUnit/SECONDS)]
          (when-not finished?
            (.destroyForcibly process)
            (.waitFor process)
            (exit! 2
                   (format "HOOK_TIMEOUT %s after %d seconds" event timeout-seconds)
                   (str "full_log=" log-file)))
          (let [status (.exitValue process)]
            (if (zero? status)
              (fs/delete-if-exists log-file)
              (let [{:keys [text size]} (read-prefix log-file max-bytes)
                    omitted (max 0 (- size max-bytes))]
                (exit! 2
                       (format "HOOK_FAILED %s exit=%d" event status)
                       (str/trim text)
                       (when (pos? omitted) (format "[%d bytes omitted]" omitted))
                       (str "full_log=" log-file))))))))))

(defn -main [& args]
  (when (empty? args)
    (exit! 2 "Usage: lifecycle_hook.bb <event> [CONTEXT_KEY CONTEXT_VALUE ...]"))
  (run-hook! (first args) (context-map (rest args))))

(apply -main *command-line-args*)
