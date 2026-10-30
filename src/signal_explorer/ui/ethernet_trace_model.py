from collections import deque
from PySide6.QtCore import QAbstractTableModel,QModelIndex,Qt,QSortFilterProxyModel
from signal_explorer.ethernet.model import decode

COLUMNS=('Timestamp','Counts','Bus Number','Direction','Source MAC','Destination MAC',
         'EtherType (hex)','VLAN','PCP','Length','Protocol','Summary','Data (hex)')
MAX_ROWS=10000


class EthernetTraceModel(QAbstractTableModel):
    def __init__(self):
        super().__init__();self.history=deque();self.latest={};self.keys=[];self.history_mode=True

    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.history if self.history_mode else self.keys)
    def columnCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(COLUMNS)
    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if orientation==Qt.Orientation.Horizontal and role==Qt.ItemDataRole.DisplayRole:return COLUMNS[section]

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        frame,count=self.history[index.row()] if self.history_mode else self.latest[self.keys[index.row()]]
        if role==Qt.ItemDataRole.UserRole:return frame
        if role!=Qt.ItemDataRole.DisplayRole:return None
        protocol,summary=decode(frame)
        return (f'{frame.timestamp:.6f}',str(count),str(frame.channel),frame.direction.upper(),frame.source,frame.destination,
                f'{frame.ethertype:04X}',str(frame.vlan) if frame.tagged else '',str(frame.pcp) if frame.tagged else '',
                str(len(frame.raw)),protocol,summary,frame.payload.hex(' ').upper())[index.column()]

    def append(self,frame):
        # A flow includes L2 identity + protocol + VLAN + IP endpoints/ports, but
        # never the changing telemetry body; fixed rows remain stable.
        protocol,summary=decode(frame)
        endpoints=summary.split(' | ')[0] if protocol in ('UDP','TCP','IPv4','ICMP') else ''
        key=(frame.channel,frame.direction,frame.source,frame.destination,frame.ethertype,frame.vlan,endpoints)
        count=self.latest.get(key,(None,0))[1]+1
        new_flow=key not in self.latest
        if new_flow and len(self.keys)>=MAX_ROWS:
            if not self.history_mode:self.beginRemoveRows(QModelIndex(),0,0)
            del self.latest[self.keys.pop(0)]
            if not self.history_mode:self.endRemoveRows()
        if self.history_mode:
            if len(self.history)>=MAX_ROWS:
                self.beginRemoveRows(QModelIndex(),0,0)
                self.history.popleft()
                self.endRemoveRows()
            row=len(self.history)
            self.beginInsertRows(QModelIndex(),row,row)
        elif new_flow:
            row=len(self.keys)
            self.beginInsertRows(QModelIndex(),row,row)
        if new_flow:self.keys.append(key)
        self.latest[key]=(frame,count);self.history.append((frame,count))
        if self.history_mode or new_flow:self.endInsertRows()
        else:
            row=self.keys.index(key)
            self.dataChanged.emit(self.index(row,0),self.index(row,len(COLUMNS)-1))
        if not self.history_mode and len(self.history)>MAX_ROWS:self.history.popleft()

    def set_history_mode(self,enabled):
        self.beginResetModel();self.history_mode=enabled;self.endResetModel()

    def clear(self):
        self.beginResetModel();self.history.clear();self.latest.clear();self.keys.clear();self.endResetModel()


class EthernetFilter(QSortFilterProxyModel):
    def __init__(self,parent=None):
        super().__init__(parent);self.terms=[];self.ethertype=None;self.direction='all'

    def configure(self,text,ethertype='',direction='all'):
        value=int(ethertype.strip(),16) if ethertype.strip() else None
        if value is not None and not 0x0600<=value<=0xffff:raise ValueError('EtherType filter must be 0600-FFFF in hex, or blank.')
        self.terms=text.casefold().split();self.ethertype=value;self.direction=direction
        self.invalidateFilter()

    def filterAcceptsRow(self,row,parent):
        source=self.sourceModel();index=source.index(row,0,parent);frame=source.data(index,Qt.ItemDataRole.UserRole)
        if self.direction!='all' and frame.direction!=self.direction:return False
        if self.ethertype is not None and frame.ethertype!=self.ethertype:return False
        if not self.terms:return True
        searchable=' '.join(str(source.data(source.index(row,col,parent))) for col in (3,4,5,6,7,10,11,12)).casefold()
        return all(term in searchable for term in self.terms)
