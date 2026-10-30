"""Private JSON-lines subprocess entry point, launched only by ScriptController."""
import json
import io
from pathlib import Path
import sys
import traceback
from signal_explorer.scripting.runtime import (
    EventWriter, ExecutionControl, ScriptContext, ScriptOutput, ScriptStopped, line_tracer,
)


def main():
    writer = EventWriter(sys.stdout)
    context = None
    try:
        start = json.loads(sys.stdin.readline())
        filename, source = start['filename'], start['source']
        if not filename.startswith('<'):
            sys.path.insert(0, str(Path(filename).parent))
        code = compile(source, filename, 'exec')
        control = ExecutionControl(writer, sys.stdin)
        # The control reader owns stdin. input() must not consume RPC replies.
        sys.stdin = io.StringIO('')
        context = ScriptContext(control, start['session'])
        sys.stdout = ScriptOutput(writer)
        sys.stderr = ScriptOutput(writer, 'stderr')
        writer.emit('started')
        sys.settrace(line_tracer(control, filename, start.get('delay_ms', 0)))
        try:
            exec(code, dict(__name__='__main__', __file__=filename, se=context))
        finally:
            sys.settrace(None)
        control.checkpoint()
        if context.failures:
            writer.emit('error',line=context.failed_line,text=f'{context.failures} check(s) failed, including caught assertions.\n')
        writer.emit('result', outcome='failed' if context.failures else 'passed' if context.checks else 'completed', checks=context.checks)
    except ScriptStopped:
        writer.emit('result', outcome='stopped', checks=context.checks if context else 0)
    except SystemExit as exc:
        sys.settrace(None)
        if exc.code in (None,0):
            writer.emit('result',outcome='failed' if context and context.failures else 'passed' if context and context.checks else 'completed',checks=context.checks if context else 0)
        else:
            writer.emit('error',line=getattr(locals().get('control'),'line',0),text=f'Script exited with status {exc.code}\n')
            writer.emit('result',outcome='failed',checks=context.checks if context else 0)
    except BaseException as exc:
        sys.settrace(None)
        line = getattr(exc, 'lineno', None) if getattr(exc, 'filename', None)==locals().get('filename') else None
        if not line:
            for frame in traceback.extract_tb(exc.__traceback__):
                if frame.filename == locals().get('filename'): line = frame.lineno
        writer.emit('error', line=line or 0, text=''.join(traceback.format_exception(exc)))
        writer.emit('result', outcome='failed', checks=context.checks if context else 0)


if __name__ == '__main__': main()
