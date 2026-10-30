"""Independent editor/test window; one shared controller owns execution."""
from pathlib import Path
import tokenize
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QToolBar, QVBoxLayout, QWidget,
)
from signal_explorer.ui.python_editor import PythonEditor


class ScriptWindow(QMainWindow):
    closed = Signal(object)
    close_main_ready = Signal()
    open_log_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.controller = controller; self.path = None; self.encoding = 'utf-8'
        self._closing = self._main_close = self._closed = False
        self.resize(1050, 800)
        self._build(); self.new_script()
        controller.changed.connect(self._refresh)
        controller.output.connect(self._output)
        controller.line_changed.connect(self._line)
        controller.finished.connect(self._finished)
        self._refresh()

    def _action(self, title, callback, shortcut=None):
        action = QAction(title, self); action.triggered.connect(callback)
        if shortcut: action.setShortcut(shortcut)
        return action

    def _build(self):
        self.editor = PythonEditor(self)
        self.console = QPlainTextEdit(); self.console.setReadOnly(True)
        self.console.setFont(QFont('Consolas', 10)); self.console.setMaximumBlockCount(5000)
        self.console.setPlaceholderText('Script output, check results and errors appear here.')
        self.editor.document().modificationChanged.connect(self._title)
        file_menu = self.menuBar().addMenu('&File')
        self.new_action = self._action('New', self.new_script, QKeySequence.StandardKey.New)
        self.open_action = self._action('Open Python Script...', self.open_script, QKeySequence.StandardKey.Open)
        self.open_log_action = self._action('Open Logfile...', self.open_log_requested.emit)
        self.save_action = self._action('Save', self.save_script, QKeySequence.StandardKey.Save)
        self.save_as_action = self._action('Save As...', self.save_as, QKeySequence.StandardKey.SaveAs)
        for action in (self.new_action, self.open_action, self.open_log_action, self.save_action, self.save_as_action):file_menu.addAction(action)
        file_menu.addSeparator(); file_menu.addAction(self._action('Close', self.close, QKeySequence.StandardKey.Close))
        edit = self.menuBar().addMenu('&Edit')
        for title, callback, shortcut in (
            ('Undo', self.editor.undo, QKeySequence.StandardKey.Undo),
            ('Redo', self.editor.redo, QKeySequence.StandardKey.Redo),
            ('Cut', self.editor.cut, QKeySequence.StandardKey.Cut),
            ('Copy', self.editor.copy, QKeySequence.StandardKey.Copy),
            ('Paste', self.editor.paste, QKeySequence.StandardKey.Paste),
            ('Select All', self.editor.selectAll, QKeySequence.StandardKey.SelectAll),
            ('Find / Replace', self._show_find, QKeySequence.StandardKey.Find),
        ): edit.addAction(self._action(title, callback, shortcut))
        bar = QToolBar('Script', self); self.addToolBar(bar)
        for action in (self.open_action, self.save_action, self.save_as_action):bar.addAction(action)
        bar.addSeparator()
        self.run = QPushButton('Run'); self.run.clicked.connect(self._run)
        self.pause = QPushButton('Pause'); self.pause.clicked.connect(self._pause)
        self.stop = QPushButton('Stop'); self.stop.clicked.connect(lambda:self.controller.stop())
        for button in (self.run, self.pause, self.stop):bar.addWidget(button)
        bar.addSeparator(); bar.addWidget(QLabel('Line delay (ms): '))
        self.delay = QSpinBox(); self.delay.setRange(0, 1000); self.delay.setValue(0)
        self.delay.setToolTip('Optional delay before each source line, for following execution. 0 runs normally.')
        bar.addWidget(self.delay)
        self.follow = QCheckBox('Follow execution'); self.follow.setChecked(True); bar.addWidget(self.follow)
        root = QWidget(); layout = QVBoxLayout(root)
        self.session = QLabel(); self.session.setWordWrap(True); layout.addWidget(self.session)
        self.find_bar = QWidget(); find = QHBoxLayout(self.find_bar); find.setContentsMargins(0,0,0,0)
        self.find_text = QLineEdit(); self.find_text.setPlaceholderText('Find (case sensitive)')
        self.replace_text = QLineEdit(); self.replace_text.setPlaceholderText('Replace with')
        find.addWidget(self.find_text); find.addWidget(self.replace_text)
        for label, callback in (('Previous', lambda:self._find(True)), ('Next', self._find),
                                ('Replace', self._replace), ('Replace All', self._replace_all),
                                ('Hide', self.find_bar.hide)):
            button=QPushButton(label);button.clicked.connect(callback);find.addWidget(button)
        self.find_text.returnPressed.connect(self._find)
        self.find_bar.hide(); layout.addWidget(self.find_bar)
        output=QWidget();output_layout=QVBoxLayout(output);output_layout.setContentsMargins(0,0,0,0)
        self.output_label=QLabel('Script Output');output_layout.addWidget(self.output_label);output_layout.addWidget(self.console)
        splitter=QSplitter(Qt.Orientation.Vertical);splitter.addWidget(self.editor);splitter.addWidget(output)
        splitter.setStretchFactor(0,3);splitter.setStretchFactor(1,1);splitter.setSizes([500,180]);layout.addWidget(splitter)
        self.note=QLabel('CAN / CAN FD scripting | Run executes the editor snapshot. Only run trusted Python files. Ethernet scripting is reserved for a later release.')
        self.note.setWordWrap(True);layout.addWidget(self.note);self.setCentralWidget(root)
        self.position=QLabel();self.statusBar().addPermanentWidget(self.position)
        self.editor.cursorPositionChanged.connect(self._position)
        self.controller.transport.connection_changed.connect(self._refresh)

    def _title(self,*_):
        name=str(self.path) if self.path else 'Untitled.py'
        self.setWindowTitle(f"{'* ' if self.editor.document().isModified() else ''}{name} - Signal Explorer Script Editor")

    def _position(self):
        cursor=self.editor.textCursor();self.position.setText(f'Line {cursor.blockNumber()+1}, Column {cursor.positionInBlock()+1}')

    def _confirm_save(self):
        if not self.editor.document().isModified():return True
        answer=QMessageBox.question(self,'Unsaved script','Save changes before closing or opening another script?',
                                   QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,
                                   QMessageBox.StandardButton.Save)
        if answer==QMessageBox.StandardButton.Cancel:return False
        if answer==QMessageBox.StandardButton.Save:return self.save_script()
        self.editor.document().setModified(False);return True

    def new_script(self,*_):
        if self.controller.active and self.controller.owner is self:return False
        if not self._confirm_save():return False
        self.path=None;self.encoding='utf-8'
        self.editor.setPlainText('# Signal Explorer provides the se test context.\n# Connect CAN / CAN FD before Run.\nse.log("Script started")\n')
        self.editor.document().setModified(False);self.editor.set_execution(0);self._title();return True

    def open_script(self,path=None):
        if self.controller.active and self.controller.owner is self:return False
        if not self._confirm_save():return False
        if not isinstance(path,(str,Path)):
            path,_=QFileDialog.getOpenFileName(self,'Open Python script',str(self.path.parent) if self.path else '', 'Python scripts (*.py);;All files (*)')
        if not path:return False
        try:
            target=Path(path).resolve()
            with tokenize.open(target) as stream:text=stream.read();encoding=stream.encoding
            self.editor.setPlainText(text);self.path=target;self.encoding=encoding
            self.editor.document().setModified(False);self.editor.set_execution(0);self._title();return True
        except Exception as exc:QMessageBox.warning(self,'Open failed',str(exc));return False

    def save_script(self,*_):
        if self.path is None:return self.save_as()
        return self._save(self.path)

    def save_as(self,*_):
        path,_=QFileDialog.getSaveFileName(self,'Save Python script',str(self.path or 'test_script.py'),'Python scripts (*.py)')
        if not path:return False
        target=Path(path)
        if target.suffix.lower()!='.py':target=Path(str(target)+'.py')
        if target.exists() and (self.path is None or target.resolve()!=self.path):
            # QFileDialog confirms an exact path, but an appended .py can name
            # a different existing file. Confirm only that additional case.
            if str(target)!=path and QMessageBox.question(self,'Replace script?',f'Replace {target}?')!=QMessageBox.StandardButton.Yes:return False
        return self._save(target.resolve())

    def _save(self,target):
        from PySide6.QtCore import QSaveFile,QIODevice
        try:
            data=self.editor.toPlainText().encode(self.encoding)
            output=QSaveFile(str(target))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError(output.errorString())
            if output.write(data)!=len(data) or not output.commit():raise OSError(output.errorString())
            self.path=target;self.editor.document().setModified(False);self._title();return True
        except Exception as exc:QMessageBox.warning(self,'Save failed',str(exc));return False

    def _run(self):
        try:
            self.console.clear();self.editor.set_execution(0)
            self.controller.start(self.editor.toPlainText(),str(self.path) if self.path else '<Signal Explorer script>',
                                  owner=self,delay_ms=self.delay.value())
        except Exception as exc:QMessageBox.warning(self,'Cannot run script',str(exc))

    def _pause(self):
        if self.controller.state in ('paused','pausing'):self.controller.resume()
        else:self.controller.pause()

    def _refresh(self,*_):
        active=self.controller.active;mine=self.controller.owner is self
        locked=active and mine
        self.editor.setReadOnly(locked)
        for action in (self.new_action,self.open_action,self.save_as_action):action.setEnabled(not locked)
        self.run.setEnabled(not active and self.controller.transport.is_connected)
        self.pause.setEnabled(locked and self.controller.state!='stopping')
        self.pause.setText('Resume' if self.controller.state in ('paused','pausing') else 'Pause')
        self.stop.setEnabled(locked and self.controller.state!='stopping');self.delay.setEnabled(not locked)
        info=self.controller.transport.session() if self.controller.transport.is_connected else {}
        self.session.setText('CAN session: '+(f"{info.get('adapter','')} | {info.get('native_channel','')} | {'CAN FD' if info.get('fd') else 'Classic CAN'}" if info else 'Disconnected - connect in the CAN / CAN FD tab.'))
        state=self.controller.state if mine else 'Another script is running' if active else 'Ready'
        self.statusBar().showMessage(state.capitalize()+(' | Editing locked during execution' if locked else ''))
        if mine:self.editor.set_execution(self.controller.line if locked or state=='failed' else 0,state,False)

    def _line(self,line):
        if self.controller.owner is self:self.editor.set_execution(line,self.controller.state,self.follow.isChecked())

    def _output(self,text):
        if self.controller.owner is not self:return
        cursor=self.console.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End);cursor.insertText(text)
        self.console.setTextCursor(cursor);self.console.ensureCursorVisible()

    def _finished(self,*_):
        if self._closing:self.close()

    def _show_find(self):self.find_bar.show();self.find_text.setFocus();self.find_text.selectAll()

    def _find(self,backward=False):
        if not self.find_text.text():return False
        flags=QTextDocument.FindFlag.FindCaseSensitively
        if backward:flags|=QTextDocument.FindFlag.FindBackward
        if self.editor.find(self.find_text.text(),flags):return True
        cursor=self.editor.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
        self.editor.setTextCursor(cursor);return self.editor.find(self.find_text.text(),flags)

    def _replace(self):
        if self.editor.isReadOnly():return
        cursor=self.editor.textCursor()
        if cursor.selectedText()==self.find_text.text():cursor.insertText(self.replace_text.text())
        self._find()

    def _replace_all(self):
        if self.editor.isReadOnly() or not self.find_text.text():return
        cursor=QTextCursor(self.editor.document());cursor.beginEditBlock()
        while True:
            found=self.editor.document().find(self.find_text.text(),cursor,QTextDocument.FindFlag.FindCaseSensitively)
            if found.isNull():break
            found.insertText(self.replace_text.text());cursor.setPosition(found.position())
        cursor.endEditBlock()

    def request_close(self,main=False):
        self._main_close=main
        return self.close()

    def closeEvent(self,event):
        if self._closed:event.accept();return
        if not self._closing and not self._confirm_save():
            self._main_close=False;event.ignore();return
        if self.controller.active and self.controller.owner is self:
            self._closing=True;self.controller.stop('File viewer closing; stopping execution.')
            event.ignore();return
        self._closed=True;event.accept();self.closed.emit(self)
        if self._main_close and self._closing:self.close_main_ready.emit()
