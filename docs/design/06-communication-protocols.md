# Communication Protocols

All inter-agent communications pass through the Tech Lead orchestrator.

## File Output Protocol
Every coder agent emits files using delimiter blocks:
```
### FILE: path/to/file.ext
<code>
```

This ensures automated, clean file extraction and saving to disk.
