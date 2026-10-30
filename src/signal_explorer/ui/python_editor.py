"""Small Python editor: syntax colors, line numbers, indentation and execution mark."""
import builtins
import keyword
import re
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget


def text_format(color, bold=False, italic=False):
    result = QTextCharFormat(); result.setForeground(QColor(color))
    if bold: result.setFontWeight(QFont.Weight.Bold)
    result.setFontItalic(italic)
    return result


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.keyword = text_format('#7c3aab', True)
        self.builtin = text_format('#006c91')
        self.number = text_format('#a34a00')
        self.string = text_format('#23723a')
        self.comment = text_format('#61736a', italic=True)

    def highlightBlock(self, text):
        self.setCurrentBlockState(0)
        for match in re.finditer(r'\b[A-Za-z_]\w*\b|\b(?:0[xX][\da-fA-F]+|\d+(?:\.\d+)?)\b', text):
            token = match.group()
            if token in keyword.kwlist or token in ('match', 'case'):
                self.setFormat(match.start(), len(token), self.keyword)
            elif token in dir(builtins) or token == 'se': self.setFormat(match.start(), len(token), self.builtin)
            elif token[0].isdigit(): self.setFormat(match.start(), len(token), self.number)
        # Strings/comments overwrite keywords inside them. Triple-quoted strings
        # carry block state, so multiline docstrings remain colored correctly.
        position = 0
        state = self.previousBlockState()
        if state in (1, 2):
            quote = "'''" if state == 1 else '"""'
            end = text.find(quote)
            if end < 0:
                self.setFormat(0, len(text), self.string); self.setCurrentBlockState(state); return
            position = end+3; self.setFormat(0, position, self.string)
        while position < len(text):
            character = text[position]
            if character == '#':
                self.setFormat(position, len(text)-position, self.comment); break
            if character in "\"'":
                quote = character*3 if text.startswith(character*3, position) else character
                start = position; position += len(quote)
                if len(quote) == 3:
                    end = text.find(quote, position)
                    if end < 0:
                        self.setFormat(start, len(text)-start, self.string)
                        self.setCurrentBlockState(1 if character == "'" else 2); return
                    position = end+3
                else:
                    while position < len(text):
                        if text[position] == '\\': position += 2; continue
                        if text[position] == character:
                            position += 1; break
                        position += 1
                self.setFormat(start, position-start, self.string)
            else: position += 1


class LineNumberArea(QWidget):
    def __init__(self, editor): super().__init__(editor); self.editor = editor
    def sizeHint(self): return QSize(self.editor.gutter_width(), 0)
    def paintEvent(self, event): self.editor.paint_gutter(event)


class PythonEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(QFont('Consolas', 11))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ')*4)
        self.setStyleSheet('QPlainTextEdit { background: #ffffff; color: #202a35; selection-background-color: #bddcf5; }')
        self.highlighter = PythonHighlighter(self.document())
        self.gutter = LineNumberArea(self)
        self.execution_line = 0; self.execution_state = 'running'
        self.blockCountChanged.connect(self._margins)
        self.updateRequest.connect(self._update_gutter)
        self._margins()

    def gutter_width(self): return 22+self.fontMetrics().horizontalAdvance('9')*len(str(max(1, self.blockCount())))
    def _margins(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self.gutter.setGeometry(QRect(self.contentsRect().left(),self.contentsRect().top(),
                                      self.gutter_width(),self.contentsRect().height()))
    def _update_gutter(self, rect, dy):
        if dy: self.gutter.scroll(0, dy)
        else: self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()): self._margins()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.gutter.setGeometry(QRect(self.contentsRect().left(), self.contentsRect().top(),
                                      self.gutter_width(), self.contentsRect().height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter); painter.fillRect(event.rect(), QColor('#edf1f5'))
        block = self.firstVisibleBlock(); top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = int(self.blockBoundingRect(block).height()); number = block.blockNumber()+1
            if block.isVisible() and top+height >= event.rect().top():
                painter.setPen(QColor('#a95000' if number == self.execution_line else '#65717f'))
                label = ('> ' if number == self.execution_line else '')+str(number)
                painter.drawText(0, top, self.gutter.width()-6, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, label)
            top += height; block = block.next()

    def set_execution(self, line, state='running', follow=True):
        self.execution_line, self.execution_state = line, state
        selections = []
        block = self.document().findBlockByNumber(line-1)
        if line > 0 and block.isValid():
            selection = QTextEdit.ExtraSelection(); selection.cursor = QTextCursor(block)
            color = '#ffd7d7' if state == 'failed' else '#ffe0ac' if state in ('paused', 'pausing') else '#fff2b3'
            selection.format.setBackground(QColor(color))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selections.append(selection)
            if follow:
                self.setTextCursor(selection.cursor); self.ensureCursorVisible()
        self.setExtraSelections(selections); self.gutter.update()

    def keyPressEvent(self, event):
        if self.isReadOnly(): return super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            cursor = self.textCursor()
            if not cursor.hasSelection() and event.key() == Qt.Key.Key_Tab:
                cursor.insertText(' '*4); return
            start = self.document().findBlock(cursor.selectionStart())
            end = self.document().findBlock(max(cursor.selectionStart(), cursor.selectionEnd()-1))
            cursor.beginEditBlock(); block = start
            while block.isValid():
                change = QTextCursor(block)
                if event.key() == Qt.Key.Key_Tab: change.insertText(' '*4)
                else:
                    amount = min(4, len(block.text())-len(block.text().lstrip(' ')))
                    change.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, amount)
                    change.removeSelectedText()
                if block == end: break
                block = block.next()
            cursor.endEditBlock(); return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cursor = self.textCursor(); before = cursor.block().text()[:cursor.positionInBlock()]
            indent = before[:len(before)-len(before.lstrip())]
            if before.rstrip().endswith(':'): indent += ' '*4
            cursor.insertText('\n'+indent); return
        super().keyPressEvent(event)
