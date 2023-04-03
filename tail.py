# Flag time regions from videos.
# 
# Run tail.py
# When prompted, select:
#   Project folder.
#   Existing or new annotation file.
# Enter video searching pattern (e.g. '*.mp4', '*/*.avi', '*/*/*.avi')
# 
# Window shows a video frame and corresponding annotation data.
# Position coordinates will be overlaid from matching DLC annotationfiles, if found in the project folder.
# Data is saved with ctrl+s and when the window is closed.
# 
# Navigation using arrow keys:
#   down/up: load previous/next video.
#   left/right: move to previous/next time step.
#   shift+left/right: move to previous/next frame.
#   ctrl+left/right: move to previous/next annotation.
#   page up/down or mouse scroll wheel: rotate annotation choice.
# 
# Annotations:
#   insert: adds a time division at the current time point.
#   delete: deletes current time division.
#   0-9: adds an id to the current time division.
#   a-z: adds a label to the current time division.
#   shift+delete: deletes selected point.
#   left click: add point annotation to a location on image.

# Keys: https://doc.qt.io/qtforpython-5/PySide2/QtCore/Qt.html
# 2022-08-19. Leonardo Molina.
# 2023-04-03. Last modified.
 
from datetime import datetime
from flexible import Flexible
from pathlib import Path, PurePath
from PyQt5 import QtGui, QtCore, QtWidgets, uic
from PyQt5.QtCore import Qt, QEvent

import bisect
import csv
import cv2
import gzip
import json
import math
import numpy as np
import os
import sys
import time


def relative(folder, paths):
    for index, path in enumerate(paths):
        path = Path(path)
        try:
            paths[index] = path.relative_to(folder).as_posix()
        except:
            paths[index] = path.as_posix()
    return paths

def Round(obj, precision):
    if isinstance(obj, float):
        return round(obj, precision)
    elif isinstance(obj, dict):
        return dict((k, Round(v, precision)) for k, v in obj.items())
    elif isinstance(obj, (list, tuple)):
        return list(map(lambda v: Round(v, precision), obj))
    return obj

class Modifiers:
    @staticmethod
    def shift():
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        return QtCore.Qt.ShiftModifier & modifiers == QtCore.Qt.ShiftModifier
    
    @staticmethod
    def control():
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        return QtCore.Qt.ControlModifier & modifiers == QtCore.Qt.ControlModifier

class Tail(QtWidgets.QMainWindow):
    def keyPressEvent(self, event):
        p = self.__private
        shift = Modifiers.shift()
        control = Modifiers.control()
        entry = self.getEntry()
        if event.key() == Qt.Key_S:
            # Save annotation data.
            if control:
                self.save()
        elif event.key() == Qt.Key_Down:
            # Load previous video file.
            p.fileId = p.fileId - 1
            if p.fileId == -1:
                p.fileId = p.nPaths - 1
            entry = self.load()
            self.seek(entry['frameId'])
            self.labelSpatial()
            self.labelTemporal()
        elif event.key() == Qt.Key_Up:
            # Load next video file.
            p.fileId = p.fileId + 1
            if p.fileId == p.nPaths:
                p.fileId = 0
            # Load last seen frame.
            entry = self.load()
            self.seek(entry['frameId'])
            self.labelSpatial()
            self.labelTemporal()
        elif event.key() == Qt.Key_Left:
            if control:
                if shift:
                    if 'points' in entry:
                        points = entry['points']
                        k = bisect.bisect_left(points['frames'], entry['frameId']) - 1
                        if k >= 0:
                            self.seek(points['frames'][k])
                            self.labelSpatial()
                else:
                    if 'notes' in entry:
                        # Move to previous note.
                        k = self.whichLabel()
                        if k == 0:
                            self.seek(entry['notes']['frames'][len(entry['notes']['frames']) - 1])
                            self.labelSpatial()
                        else:
                            self.seek((entry['notes']['frames'][(k - 1) % len(entry['notes']['frames'])] - 1) % p.nFrames)
                            self.labelSpatial()
            else:
                # Advance frame position by a given step.
                step = 1 if shift else p.frameStep
                entry['frameId'] = max(entry['frameId'] - step, 1)
                self.seek(entry['frameId'])
                self.labelSpatial()
        elif event.key() == Qt.Key_Right:
            if control:
                if shift:
                    if 'points' in entry:
                        points = entry['points']
                        k = bisect.bisect_right(points['frames'], entry['frameId']) + 1
                        if k < len(points['frames']):
                            self.seek(points['frames'][k])
                            self.labelSpatial()
                else:
                    if 'notes' in entry:
                        # Advance to next note.
                        k = self.whichLabel()
                        if k == len(entry['notes']['labels']) - 1:
                            self.seek(1)
                            self.labelSpatial()
                        else:
                            self.seek(entry['notes']['frames'][k % len(entry['notes']['frames'])])
                            self.labelSpatial()
            else:
                # Advance frame position by a given step.
                step = 1 if shift else p.frameStep
                entry['frameId'] = min(entry['frameId'] + step, p.nFrames)
                self.seek(entry['frameId'])
                self.labelSpatial()
        elif event.key() == Qt.Key_Home:
            # Go to first frame.
            self.seek(1)
            self.labelSpatial()
        elif event.key() == Qt.Key_End:
            # Go to last frame.
            self.seek(p.nFrames - 1)
            self.labelSpatial()
        elif event.key() == Qt.Key_Insert:
            # Insert epoch divider.
            self.insert()
        elif event.key() == Qt.Key_Delete:
            # Delete epoch divider and annotation that follows.
            # There is at least one annotation in a file, which defaults to 0 from start to end.
            if shift:
                if 'points' in entry:
                    points = entry['points']
                    k = next((k for k, (frame, label) in enumerate(zip(points['frames'], points['labels'])) if frame == entry['frameId'] and label == p.pointLabel), None)
                    if k is not None:
                        del points['frames'][k]
                        del points['labels'][k]
                        del points['x'][k]
                        del points['y'][k]
                    x = y = c = []
                    if entry['frameId'] in points['frames']:
                        x, y, labels, frames = zip(*filter(lambda z: z[3] == entry['frameId'], zip(points['x'], points['y'], points['labels'], points['frames'])))
                        c = [p.palette[i] for i in labels]
                    p.board.setPoints(x, y, c)
            else:
                self.remove()
        elif event.key() == Qt.Key_PageUp:
            if len(p.data['labels']) > 0:
                # Change current body part.
                p.pointLabel = (p.pointLabel - 1) % len(p.data['labels'])
                self.labelSpatial()
        elif event.key() == Qt.Key_PageDown:
            if len(p.data['labels']) > 0:
                # Change current body part.
                p.pointLabel = (p.pointLabel + 1) % len(p.data['labels'])
                self.labelSpatial()
        super().keyPressEvent(event)
    
    def insert(self):
        p = self.__private
        # Adding an epoch divider inserts a corresponding 0-label.
        entry = self.getEntry()
        if 'notes' not in entry:
            entry['notes'] = {
                'frames': [entry['frameId']],
                'labels': ['']
            }
            k = 0
        elif entry['frameId'] not in entry['notes']['frames']:
            bisect.insort(entry['notes']['frames'], entry['frameId'])
            k = entry['notes']['frames'].index(entry['frameId'])
            entry['notes']['labels'].insert(k, '')
        else:
            k = -1
        self.labelTemporal()
        if k >= 0:
            item = p.ui.timeLabelsList.item(k)
            p.ui.timeLabelsList.editItem(item)
        self.labelSpatial()
    
    def remove(self):
        p = self.__private
        entry = self.getEntry()
        if 'notes' in entry:
            if len(entry['notes']['frames']) == 1:
                del entry['notes']
            else:
                k = self.whichLabel()
                del entry['notes']['frames'][k]
                del entry['notes']['labels'][k]
        self.labelTemporal()
        self.labelSpatial()
            
    # frames: 0[   100   200   300   ]500
    def whichLabel(self):
        p = self.__private
        # Cut frame to the left of current frame.
        entry = self.getEntry()
        found = False
        if 'notes' in entry:
            for i in reversed(range(len(entry['notes']['frames']))):
                if entry['frameId'] >= entry['notes']['frames'][i]:
                    k = i
                    found = True
                    break
        return k if found else -1
        
    def highlight(self):
        # Highlight temporal note.
        p = self.__private
        p.ui.timeLabelsList.clearSelection()
        k = self.whichLabel()
        for i in range(p.ui.timeLabelsList.count()):
            item = p.ui.timeLabelsList.item(i)
            item.setBackground(Qt.lightGray if i == k else Qt.white)
    
    def labelSpatial(self):
        p = self.__private
        # Spatial annotations.
        if len(p.data['labels']) > 0:
            index = p.pointLabel % len(p.data['labels'])
            p.ui.pointLabelsList.setCurrentRow(index)
        
        if p.nPaths > 0:
            entry = self.getEntry()
            fileLabelText = '[%d:%d / %d:%d] %s' % (p.fileId + 1, p.nPaths, entry['frameId'], p.nFrames, entry['path'])
            self.highlight()
        else:
            fileLabelText = '[0:0 / 0:0] No files found with "%s"' % str(Path(p.folder) / p.glob)
        p.ui.fileLabel.setText(fileLabelText)
        
    def labelTemporal(self):
        p = self.__private
        # Temporal annotations.
        if p.nPaths > 0:
            entry = self.getEntry()
            p.ui.timeLabelsList.blockSignals(True)
            if 'notes' in entry:
                p.ui.timeLabelsList.clear()
                for label in entry['notes']['labels']:
                    item = QtWidgets.QListWidgetItem(label)
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    p.ui.timeLabelsList.addItem(item)
                self.highlight()
            else:
                p.ui.timeLabelsList.clear()
            p.ui.timeLabelsList.blockSignals(False)
        
        
    def load(self):
        p = self.__private
        entry = self.getEntry()
        videoPath = Path(entry['path'])
        if not videoPath.is_absolute():
            videoPath = Path(p.folder) / videoPath
        
        # Release if already opened.
        if p.stream is not None:
            p.stream.release()
        
        # Nullify if can't be opened.
        p.stream = None
        p.nFrames = 0
        p.frameStep = 0
        if videoPath.is_file():
            size = os.stat(str(videoPath)).st_size
            if size > 1000:
                # Load video file.
                p.stream = cv2.VideoCapture(str(videoPath))
                fps = p.stream.get(cv2.CAP_PROP_FPS)
                p.nFrames = p.stream.get(cv2.CAP_PROP_FRAME_COUNT)
                p.frameStep = round(fps * p.secondStep)
        return entry
        
    def seek(self, frameId):
        p = self.__private
        frameId = round(frameId)
        success = False
        nAttempts = 5
        attemptPause = 1e-5
        if p.stream is not None and frameId <= p.nFrames:
            if frameId > p.stream.get(cv2.CAP_PROP_POS_FRAMES):
                attempts = 0
                while p.stream.get(cv2.CAP_PROP_POS_FRAMES) < frameId:
                    if p.stream.grab():
                        attempts = 0
                    else:
                        time.sleep(attemptPause)
                        attempts += 1
                        if attempts == nAttempts:
                            break
            elif frameId < p.stream.get(cv2.CAP_PROP_POS_FRAMES):
                p.stream.set(cv2.CAP_PROP_POS_FRAMES, frameId - 1)
                for i in range(nAttempts):
                    if p.stream.grab():
                        break
                    else:
                        time.sleep(attemptPause)
                
            if frameId == p.stream.get(cv2.CAP_PROP_POS_FRAMES):
                for i in range(nAttempts):
                    success, image = p.stream.retrieve()
                    if success:
                        break
                    else:
                        time.sleep(attemptPause)
            else:
                print('Failed to grab or set position')
        if success:
            entry = self.getEntry()
            entry['frameId'] = frameId
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
            # Update points with image change.
            x = y = c = []
            if 'points' in entry:
                points = entry['points']
                if frameId in points['frames']:
                    x, y, labels, frames = zip(*filter(lambda z: z[3] == frameId, zip(points['x'], points['y'], points['labels'], points['frames'])))
                    c = [p.palette[label] for label in labels]
        else:
            image = np.zeros((100, 100, 3), dtype=np.uint8)
            x = y = c = []
        p.board.setPoints(x, y, c)
        p.board.setImage(image)
        return success
    
    def boardMouseEvent(self, data):
        p = self.__private
        if data.category == 'release':
            entry = self.getEntry()
            shift = Modifiers.shift()
            control = Modifiers.control()
            # Add when left click alone.
            if data.event.button() == Qt.LeftButton and not shift and not control:
                if 'points' not in entry:
                    entry['points'] = {'frames':[], 'labels':[], 'x':[], 'y':[], 'p':[]}
                points = entry['points']
                if 'p' not in points:
                    points['p'] = [1] * len(points['frames'])
                    
                k = next((k for k, (frame, label) in enumerate(zip(points['frames'], points['labels'])) if frame == entry['frameId'] and label == p.pointLabel), None)
                if k is None:
                    k = bisect.bisect(points['frames'], entry['frameId'])
                    points['frames'].insert(k, entry['frameId'])
                    points['labels'].insert(k, p.pointLabel)
                    points['x'].insert(k, data.x)
                    points['y'].insert(k, data.y)
                    points['p'].insert(k, 1)
                else:
                    points['frames'][k] = entry['frameId']
                    points['labels'][k] = p.pointLabel
                    points['x'][k] = data.x
                    points['y'][k] = data.y
                    points['p'][k] = 1
            
            x = y = c = []
            if 'points' in entry:
                points = entry['points']
                if entry['frameId'] in points['frames']:
                    x, y, labels, frames = zip(*filter(lambda z: z[3] == entry['frameId'], zip(points['x'], points['y'], points['labels'], points['frames'])))
                    c = [p.palette[i] for i in labels]
            p.board.setPoints(x, y, c)
        elif data.category == 'wheel':
            if len(p.data['labels']) > 0:
                delta = 1 if data.event.angleDelta().y() <= 0 else -1
                p.pointLabel = (p.pointLabel + delta) % len(p.data['labels'])
                self.labelSpatial()
    
    def save(self):
        p = self.__private
        
        path = Path(p.output)
        if p.compress:
            if path.suffix.lower() != '.gz':
                path = path.with_suffix('.gz')
            with gzip.open(str(path), 'wt', encoding='utf-8') as file:
                file.write(json.dumps(p.data))
        else:
            if path.suffix.lower() == '.gz':
                path = path.parent / path.stem
            if path.suffix.lower() != '.json':
                path = path.with_suffix('.json')
            with open(str(path), 'w', encoding='utf-8') as file:
                file.write(json.dumps(p.data, indent=2))
        
    def getEntry(self):
        p = self.__private
        return p.data['entries'][p.fileId]
        
    def __init__(self, file, folder=None, videos=[], compress=True, *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = self.__private = Flexible()
        p.running = True
        p.stream = None
        p.secondStep = 0.10
        p.pointLabel = 0
        p.compress = compress
        p.palette = ('#2f4f4f', '#8b4513', '#191970', '#006400', '#ff0000', '#ffa500', '#ffff00', '#00ff00', '#00bfff', '#0000ff', '#ff00ff', '#dda0dd', '#ff1493', '#98fb98', '#ffdead') # !!
        
        p.output = file
        if folder is None:
            folder = Path(file).parent.as_posix()
        else:
            folder = Path(folder).as_posix()
        p.folder = folder
        
        success = True
        if folder is None or not Path(folder).is_dir():
            print('Project folder not found.')
            success = False
        else:
            # Create new file or parse contents if an existing one is provided.
            if Path(p.output).is_file():
                try:
                    if Path(p.output).suffix.lower() == '.gz':
                        file = gzip.open(p.output, 'rt', encoding='utf-8')
                    else:
                        file = open(p.output, 'rt', encoding='utf-8')
                    p.data = json.load(file)
                    file.close()
                except Exception as ex:
                    success = False
                    exception = str(ex)
                    print('Could not load data from "%s" ==> %s' % (p.output, exception))
            else:
                p.data = {'labels':[], 'entries':[]}
            
            if success:
                # Make paths relative to folder when possible.
                paths = list([entry['path'] for entry in p.data['entries']])
                paths = relative(folder, paths)
                for path, entry in zip(paths, p.data['entries']):
                    entry['path'] = path
                videos = relative(folder, videos)
                
                # Remove duplicates.
                # pairs = dict((path, index) for index, path in enumerate(paths))
                # unique = set(pairs)
                # uid = [pairs[key] for key in unique]
                # paths = [paths[i] for i in uid]
                # p.entries = [p.entries[i] for i in uid]
                
                # Maintain existing data.
                difference = list(set(videos).difference(paths))
                paths.extend(difference)
                for path in difference:
                    p.data['entries'].append({'path': path})
                
                for entry in p.data['entries']:
                    if 'frameId' not in entry:
                        entry['frameId'] = 1
                
                # Initialize.
                p.nPaths = len(paths)
                if p.nPaths == 0:
                    success = False
                    print('No files available')
                    p.ui = uic.loadUi('tail.ui', self)
                    QtCore.QMetaObject.invokeMethod(self, 'close', Qt.QueuedConnection)
                else:
                    p.ui = uic.loadUi('tail.ui', self)
                    # Setup pointLabelsList with editable elements.
                    for label in list(p.data['labels']):
                        item = QtWidgets.QListWidgetItem(label)
                        item.setFlags(item.flags() | Qt.ItemIsEditable)
                        p.ui.pointLabelsList.addItem(item)
                    p.ui.pointLabelsList.itemChanged.connect(lambda item: self.editItem(combo=p.ui.pointLabelsList, text=item.text()))
                    p.ui.pointLabelsList.setCurrentRow(0)
                    p.ui.pointLabelsList.itemSelectionChanged.connect(lambda : self.selectionChanged(p.ui.pointLabelsList))
                    p.ui.addSpatialButton.clicked.connect(lambda checked : self.addItem(combo=p.ui.pointLabelsList, text=''))
                    p.ui.removeSpatialButton.clicked.connect(lambda checked : self.removeItem(combo=p.ui.pointLabelsList))
                    # Setup timeLabelsList.
                    p.ui.timeLabelsList.itemChanged.connect(lambda item: self.editItem(combo=p.ui.timeLabelsList, text=item.text()))
                    p.ui.timeLabelsList.itemSelectionChanged.connect(lambda : self.selectionChanged(p.ui.timeLabelsList))
                    p.ui.addTimeButton.clicked.connect(lambda checked : self.addItem(combo=p.ui.timeLabelsList, text=''))
                    p.ui.removeTimeButton.clicked.connect(lambda checked : self.removeItem(combo=p.ui.timeLabelsList))
                    
                    p.board = Board(self)
                    p.board.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.MinimumExpanding)
                    p.board.setMinimumSize(QtCore.QSize(0, self.height() // 2))
                    p.board.mouse.connect(self.boardMouseEvent)
                    p.ui.WindowLayout.insertWidget(0, p.board)
                    p.fileId = 0
                    self.setWindowTitle('Tail labeler')
                    self.show()
                    if p.nPaths > 0:
                        entry = self.load()
                        self.seek(entry['frameId'])
                    self.labelSpatial()
                    self.labelTemporal()
    
    def addItem(self, combo, text=''):
        p = self.__private
        if combo == p.ui.pointLabelsList:
            if text not in p.data['labels']:
                p.data['labels'].append(text)
                item = QtWidgets.QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                combo.addItem(item)
                QtCore.QCoreApplication.processEvents()
                p.pointLabel = len(p.data['labels']) - 1
                combo.editItem(item)
                self.labelSpatial()
        elif combo == p.ui.timeLabelsList:
            self.insert()
    
    def removeItem(self, combo):
        p = self.__private
        if combo == p.ui.pointLabelsList:
            if len(p.data['labels']) > 0:
                index = combo.row(combo.currentItem())
                del p.data['labels'][index]
                combo.takeItem(index)
                p.pointLabel = max(index - 1, 0)
                if len(p.data['labels']) > 0:
                    combo.setCurrentRow(p.pointLabel)
                    self.labelSpatial()
        elif combo == p.ui.timeLabelsList:
            self.remove()
    
    def selectionChanged(self, combo):
        p = self.__private
        entry = self.getEntry()
        if combo == p.ui.pointLabelsList:
            p.pointLabel = combo.row(combo.currentItem())
        elif combo == p.ui.timeLabelsList:
            k = combo.row(combo.currentItem())
            self.seek(entry['notes']['frames'][k])
            self.labelSpatial()
    
    def editItem(self, combo, text):
        p = self.__private
        entry = self.getEntry()
        if combo == p.ui.pointLabelsList:
            p.data['labels'][p.pointLabel] = text
        elif combo == p.ui.timeLabelsList:
            k = combo.row(combo.currentItem())
            if k >= 0:
                entry['notes']['labels'][k] = text
    
    def closeEvent(self, event):
        p = self.__private
        p.running = False
        if p.stream is not None:
            p.stream.release()
        self.save()
        event.accept()

class Canvas(QtWidgets.QLabel):
    paint = QtCore.pyqtSignal(object)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def paintEvent(self, event):
        self.paint.emit(event)
        super().paintEvent(event)

class EventData(object):
    def __init__(self, event, category, x, y):
        self.event = event
        self.category = category
        self.x = x
        self.y = y

class Board(QtWidgets.QWidget):
    # Mouse position relative to widget.
    mouse = QtCore.pyqtSignal(object)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = self.__private = Flexible()
        p.x = []
        p.y = []
        p.colors = []
        p.imageLabel = QtWidgets.QLabel(self)
        p.canvas = Canvas(self)
        p.canvas.paint.connect(self.onCanvasPaint)
        self.setImage(np.zeros((100, 100, 3), dtype=np.uint8))
    
    def resizeEvent(self, event):
        # Match labels' geometry with parent.
        p = self.__private
        width = event.size().width()
        height = event.size().height()
        p.imageLabel.setGeometry(0, 0, width, height)
        p.canvas.setGeometry(0, 0, width, height)
        self.refreshPixmap(width)
        p.canvas.update()
        super().resizeEvent(event)
    
    def setImage(self, image):
        # Replace currently displayed image (pixmap).
        p = self.__private
        p.qImage = QtGui.QImage(image.data, image.shape[1], image.shape[0], image.strides[0], QtGui.QImage.Format_RGB888)
        p.qPixmap = QtGui.QPixmap.fromImage(p.qImage)
        self.refreshPixmap(self.width())
        
    def refreshPixmap(self, width):
        # Reescale currently displayed pixmap to a given width.
        p = self.__private
        p.scaledPixmap = p.qPixmap.scaledToWidth(width)
        p.imageLabel.setPixmap(p.scaledPixmap)
    
    def setPoints(self, x, y, colors):
        # Remember new points and repaint.
        p = self.__private
        p.x, p.y, p.colors = x, y, colors
        p.canvas.update()
    
    def onCanvasPaint(self, event):
        # Redraw annotations when canvas' requires it.
        p = self.__private
        painter = QtGui.QPainter(p.canvas)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        circleSize = 12
        
        for x, y, color in zip(p.x, p.y, p.colors):
            x, y = self.pixmapToWindow(x, y)
            pen = QtGui.QPen(QtGui.QColor(color))
            pen.setWidth(4)
            painter.setPen(pen)
            #painter.setBrush(QtGui.QColor(color))
            painter.drawEllipse(QtCore.QPoint(round(x), round(y)), circleSize, circleSize)
        
        painter.setBrush(Qt.NoBrush)
        pen = QtGui.QPen(Qt.red, 2, Qt.SolidLine)
        painter.setPen(pen)
        x, y = self.pixmapToWindow(0, 0)
        painter.drawRect(round(x), round(y), p.scaledPixmap.width(), p.scaledPixmap.height())
        painter.end()
    
    def mousePressEvent(self, event):
        self.mouse.emit(EventData(event, 'press', *self.windowToPixmap(event.pos().x(), event.pos().y())))
        super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        self.mouse.emit(EventData(event, 'release', *self.windowToPixmap(event.pos().x(), event.pos().y())))
        super().mouseReleaseEvent(event)
    
    def mouseMoveEvent(self, event):
        self.mouse.emit(EventData(event, 'move', *self.windowToPixmap(event.pos().x(), event.pos().y())))
        super().mouseMoveEvent(event)
        
    def wheelEvent(self,event):
        self.mouse.emit(EventData(event, 'wheel', *self.windowToPixmap(event.pos().x(), event.pos().y())))
        super().wheelEvent(event)
    
    def windowToPixmap(self, x, y):
        p = self.__private
        # Remove pixmap offset within widget.
        x -= 0.5 * (self.width() - p.scaledPixmap.width())
        y -= 0.5 * (self.height() - p.scaledPixmap.height())
        # Rescale to pixmap dimensions.
        x *= p.qPixmap.width() / p.scaledPixmap.width()
        y *= p.qPixmap.height() / p.scaledPixmap.height()
        return x, y
        
    def pixmapToWindow(self, x, y):
        p = self.__private
        # Rescale to widget dimensions.
        x *= p.scaledPixmap.width() / p.qPixmap.width()
        y *= p.scaledPixmap.height() / p.qPixmap.height()
        # Add pixmap offset within widget.
        x += 0.5 * (self.width() - p.scaledPixmap.width())
        y += 0.5 * (self.height() - p.scaledPixmap.height())
        return x, y
    
if __name__ == '__main__':
    # Project folder; video paths are relative to this path.
    projectFolder = r'H:\g010\tests'
    projectFile = r'H:\g010\annotation-test.json.gz'
    
    # Search recursively for new files matching the extension.
    glob = '*-C.mp4'
    videoList = list(Path(projectFolder).glob(glob))
    videoList = [path.as_posix() for path in videoList]
    
    # Exclude video files already exported with DLC.
    videoList = [path for path in videoList if 'DLC' not in path]
    
    app = QtWidgets.QApplication(sys.argv)
    Tail(file=projectFile, folder=projectFolder, videos=videoList, compress=True)
    result = app.exec()
    sys.exit(result)