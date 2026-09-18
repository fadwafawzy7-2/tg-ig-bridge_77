"""
Phase 4 — Product Parser & Duplicate Detection.

- `schemas.py`          — `ParsedProductFields` DTO
- `text_parser.py`      — pure, deterministic text parsing (NO AI, NO I/O)
- `product_service.py`  — DB orchestration: parse -> dedup-check -> create
                           or link
- `parser_runner.py`    — batch driver over PENDING source_messages; CLI
                           entrypoint

Product specs are extracted ONLY from `source_messages.raw_text` — never
from images/media. Nothing is invented: a field with no clear textual
signal is left `None` rather than guessed.
"""
