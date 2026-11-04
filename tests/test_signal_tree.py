import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import can
import cantools
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QAbstractItemModelTester
from signal_explorer.ui.trace_model import SignalTreeModel, IdFilter
from signal_explorer.ui.signal_tree_view import SignalTreeView

app = QApplication.instance() or QApplication([])


def frame(value=65, identifier=0x100):
    return can.Message(arbitration_id=identifier, is_extended_id=False,
                       data=[5, value, 0, 0, 0, 0, 0, 0])


def setup_tree(history=False, limit=100):
    model = SignalTreeModel(history_mode=history, max_history=limit)
    model.set_database(cantools.database.load_file('examples/demo.dbc'))
    proxy = IdFilter()
    proxy.setSourceModel(model)
    view = SignalTreeView()
    view.setModel(proxy)
    proxy.filteringAboutToChange.connect(view.collapseAll)
    view.expanded.connect(lambda i: model.set_expanded(proxy.mapToSource(i), True))
    view.collapsed.connect(lambda i: model.set_expanded(proxy.mapToSource(i), False))
    return model, proxy, view


def test_expansion_summary_and_live_values():
    model, proxy, view = setup_tree()
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
    model.append_message(frame())
    parent = proxy.index(0, 0)
    summary = proxy.index(0, 9)
    original = summary.data()
    assert 'Temperature=25 degC' in original
    assert proxy.hasChildren(parent)
    view.expand(parent)
    assert summary.data() == ''
    children = {proxy.index(r, 0, parent).data(): r for r in range(proxy.rowCount(parent))}
    row = children['Temperature']
    child = proxy.index(row, 9, parent)
    assert child.data() == '25 degC'
    assert child.parent() == parent
    model.append_message(frame(66))
    assert view.isExpanded(parent)
    assert summary.data() == ''
    assert child.data() == '26 degC'
    view.collapse(parent)
    assert 'Temperature=26 degC' in summary.data()
    proxy.set_expression('100')
    assert proxy.rowCount(parent) == len(children)
    view.expand(parent)
    proxy.set_expression('200')
    assert proxy.rowCount() == 0
    proxy.set_expression('100')
    assert 'Temperature=26 degC' in proxy.index(0, 9).data()
    view.close()


def test_history_eviction_dbc_changes_and_unknown_frames():
    model, proxy, view = setup_tree(True, 2)
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
    model.append_message(frame())
    model.append_message(frame(66))
    model.append_message(frame(67))
    parent = model.index(0, 0)
    values = [model.index(r, 9, parent).data() for r in range(model.rowCount(parent))]
    assert '26 degC' in values
    assert model.parent(model.index(0, 0, parent)) == parent
    model.set_database(None)
    assert model.rowCount(model.index(0, 0)) == 0
    model.set_database(cantools.database.load_file('examples/demo.dbc'))
    assert model.rowCount(model.index(0, 0)) > 0
    model.append_message(frame(identifier=0x777))
    assert not model.hasChildren(model.index(1, 0))
    model.set_history_mode(False)
    model.clear()
    assert model.rowCount() == 0
    view.close()
