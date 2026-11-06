"""Privacy-conscious trace logging for the Windows footprint-selector debug build."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import traceback


LOG_PATH = Path(tempfile.gettempdir()) / 'connector-interposer-workflow-footprint-debug.log'
_LOCK = threading.Lock()


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def token(value):
    """Identify a value across trace lines without recording its contents."""
    text = str(value or '')
    return {
        'present': bool(text),
        'length': len(text),
        'sha256_12': hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:12],
    }


def path_facts(value):
    """Record filesystem behavior without recording a project or library path."""
    text = str(value or '')
    path = Path(text) if text else None
    facts = token(text)
    facts.update({
        'suffix': path.suffix.casefold() if path else '',
        'absolute': path.is_absolute() if path else False,
        'exists': path.exists() if path else False,
        'is_file': path.is_file() if path else False,
        'has_spaces': ' ' in text,
        'has_non_ascii': any(ord(character) > 127 for character in text),
        'looks_unc': text.startswith(('\\\\', '//')),
    })
    return facts


def selection_facts(selection):
    selection = dict(selection or {})
    return {
        'library_id': token(selection.get('library_id', '')),
        'path': path_facts(selection.get('path', '')),
        'has_sha256': bool(selection.get('sha256')),
        'keys': sorted(selection),
    }


def reset(**context):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        LOG_PATH.write_text('', encoding='utf-8')
    trace('session_start', log_path=str(LOG_PATH), **context)


def trace(event, **fields):
    record = {'time_utc': _stamp(), 'event': str(event), **fields}
    line = json.dumps(record, sort_keys=True, ensure_ascii=True, default=str)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, LOG_PATH.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
            stream.flush()
    except Exception:
        # Diagnostics must never prevent the plugin from opening or closing.
        return


def trace_exception(event, error, **fields):
    trace(
        event,
        error_type=type(error).__name__,
        error_text=str(error),
        traceback=''.join(traceback.format_exception(type(error), error, error.__traceback__)),
        **fields,
    )
