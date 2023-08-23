from PyQt5.QtCore import QMutex, Qt, QAbstractListModel, QVariant, pyqtSignal
from PyQt5.QtWidgets import QApplication, QListView, QVBoxLayout, QWidget

class ListWidget(QListView):
    onValueChange = pyqtSignal(object, int, object)
    onSelectionChanged = pyqtSignal(object, int)

    def __init__(self, data=[], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__mutex = QMutex()
        self.setData(data)

    def clear(self):
        self.setData([])

    def setData(self, data):
        if isinstance(data, list) and not isinstance(self.model(), TableModel):
            self.setModel(TableModel(data))
            self.model().dataChanged.connect(self.__onDataChanged)
            self.selectionModel().currentChanged.connect(self.__currentChanged)
        elif isinstance(data, tuple) and not isinstance(self.model(), ColumnModel):
            self.setModel(ColumnModel(data))
            self.model().dataChanged.connect(self.__onDataChanged)
            self.selectionModel().currentChanged.connect(self.__currentChanged)
        self.model().table = data
        self.refresh()

    def __currentChanged(self, current, _):
        if self.__mutex.tryLock():
            self.onSelectionChanged.emit(self, current.row())
            self.__mutex.unlock()

    def index(self, row):
        return self.model().index(row, 0)

    def startEditing(self, row):
        self.edit(self.index(row))

    def select(self, row):
        self.__mutex.lock()
        self.setCurrentIndex(self.index(row))
        self.__mutex.unlock()

    def selected(self):
        return self.currentIndex().row()
    
    def refresh(self, rows=None):
        if rows is None:
            self.model().modelReset.emit()
        else:
            if not isinstance(rows, list):
                rows = [rows]
            for row in rows:
                index = self.model().index(row, 0)
                self.model().dataChanged.emit(index, index, [Qt.UserRole])
                index = self.model().index(row, 1)
                self.model().dataChanged.emit(index, index, [Qt.UserRole])
        
    def __onDataChanged(self, topLeft, _, roles):
        if Qt.EditRole in roles:
            self.onValueChange.emit(self, topLeft.row(), self.model().data(topLeft, Qt.EditRole))
        
class TableModel(QAbstractListModel):
    def __init__(self, table, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.table = table
        self.__selectedIndex = None

    def rowCount(self, *args):
        return len(self.table)

    def setData(self, index, value, role=Qt.EditRole):
        if index.isValid() and role == Qt.EditRole:
            self.table[index.row()] = (self.table[index.row()][0], value)
            self.dataChanged.emit(index, index, [Qt.EditRole])
            success = True
        else:
            success = False
        return success

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if index.isValid() and row < len(self.table):
            if role == Qt.DisplayRole:
                output = f"{self.table[row][0]} - {self.table[row][1]}"
            elif role == Qt.EditRole:
                output = self.table[row][1]
            else:
                output = QVariant()
        else:
            output = QVariant()
        return output

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable if index.isValid() else Qt.NoItemFlags


class ColumnModel(QAbstractListModel):
    def __init__(self, table, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.table = table
        self.__selectedIndex = None

    def rowCount(self, *args):
        return len(self.table[0])

    def setData(self, index, value, role=Qt.EditRole):
        if index.isValid() and role == Qt.EditRole:
            self.table[1][index.row()] = value
            self.dataChanged.emit(index, index, [Qt.EditRole])
            success = True
        else:
            success = False
        return success

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if index.isValid() and row < len(self.table[0]):
            if role == Qt.DisplayRole:
                output = f"{self.table[0][row]} - {self.table[1][row]}"
            elif role == Qt.EditRole:
                output = self.table[1][row]
            else:
                output = QVariant()
        else:
            output = QVariant()
        return output

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable if index.isValid() else Qt.NoItemFlags

if __name__ == "__main__":
    app = QApplication([])
    
    
    table = [
        ['Read Only 0', 'Editable 0'],
        ["Read Only 1", "Editable 1"],
        ["Read Only 2", "Editable 2"],
    ]
    widget = ListWidget(table)
    widget.onValueChange.connect(lambda source, row, value : print(f'%d:%s' % (row, value)))
    widget.onSelectionChanged.connect(lambda source, row : print(f'row:{row}'))
    table[1] = ("READ ONLY 1", 'EDITABLE 1')
    widget.refresh()
    table.append(['RO 3', 'E 3'])
    widget.refresh(3)
    widget.startEditing(2)
    widget.select(2)

    main = QWidget()
    layout = QVBoxLayout(main)
    layout.addWidget(widget)

    main.show()
    app.exec_()

    table = (
        ['Read Only 0', 'Read Only 1', 'Read Only 2'],
        ['Editable 0', 'Editable 1', 'Editable 2']
    )
    widget = ListWidget(table)
    widget.setData(table)
    widget.onRowChange.connect(lambda source, row, value : print(f'%d:%s' % (row, value)))
    widget.onSelectionChanged.connect(lambda source, row : print(f'row:{row}'))
    table[0][0] = "READ ONLY 10"
    table[1][0] = "EDITABLE 10"
    widget.refresh()
    table[0].append('RO 30')
    table[1].append('E 30')
    widget.refresh(3)
    widget.edit(2)
    widget.select(2)
    widget.show()
    app.exec_()
