# Resumable progress journal

Add a sidecar journal so bounded transcript ingestion can resume after a restart. Checkpoints cache committed progress; the database stays authoritative. The refresh command constructs ProgressJournal before opening the database, with no surrounding exception handler. A source with no checkpoint has its derived rows reset and is re-read from offset zero, so reconstruction is supported. Service restarts retry the same state directory.

This excerpt keeps the constructor with stdlib imports and the version constant. Other journal methods and adapters are omitted. Only checkpoint loading is in scope. Line numbers refer to progress_journal.py.
