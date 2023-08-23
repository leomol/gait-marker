# Label time events and pixels in video frames.
# 
# Run GaitMarker.py
# When prompted, select:
#   Project folder (video paths are relative to this)
#   Existing or new annotation file.
# 
# Window shows a video frame and corresponding annotation data.
# Data is saved with ctrl+s and when the window is closed.
# First frame index is 1. Keyframes are indexed during video playback and saved into the output file.
# 
# Changing video file:
#   ctrl + down / up or page up / down: load previous/next video.
# 
# Advance time - skip frames:
#   home / end: move to start/end of the video file.
#   left / right: move to previous/next time step.
#   shift + left / right: move to previous/next frame.
# 
# Advance time - jump through temporal annotations:
#   ctrl + left / right: move to previous/next time annotation.
#   mouse scroll wheel (over temporal annotation list): move to previous/next frame.
#   shift + mouse scroll wheel (over temporal annotation list): move to previous/next frame (fine step).
#   ctrl + mouse scroll wheel (over temporal annotation list): move to previous/next annotation.
# 
# Advance time - jump through frames with spatial annotations
#   mouse scroll wheel (over image): rotate through spatial annotation choices.
#   ctrl + mouse scroll wheel (over spatial annotation list or image): move to previous/next annotation.
# 
# Temporal annotations:
#   insert: adds a time division at the current time point.
#   delete: deletes current time division.
#   F2 or double-click on annotation: edit selected item.
#   alt + left / right: move current annotation to landing frame.
#   alt + shift + left / right: move current annotation to landing frame (fine step).
#   alt + enter: move current annotation to this frame.
#   ctrl + shift + left / right: move to previous/next annotation of the same kind. If probabilities are available, the threshold value is used in the search.
#   ctrl + enter: set p to 1, if p in entry['events']
# 
# Spatial annotations:
#   shift + delete: deletes current spatial annotation.
#   left click: set current spatial annotation to current frame at pointer position.
# 
# Example output file:
#   {
#   	"labels": ["Nose", "Tail"],
#   	"entries":
#   		[
#   			{
#   				"path": "20230331-111608-211200006-C.mp4",
#   				"frameId": 1,
#   				"points":
#   					{
#   						"frames": [4, 4, 136],
#   						"labels": [0, 1, 1],
#   						"x": [67.2504, 476.0070, 412.9597],
#   						"y": [303.6777, 302.6269, 492.8196],
#   						"p": [1, 1, 1]
#   					},
#   				"events":
#   					{
#   						"frames": [4, 64],
#   						"labels": ["label1", "label2"],
#                           "p": [1, 1]
#   					}
#                   "keyframes": [0, 30, 60]
#   			},
#   		]
#   }

# 2022-08-19. Leonardo Molina.
# 2023-08-23. Last modified.
version = '2023-08-23'

from bisect import bisect, bisect_left, bisect_right
from flexible import Flexible
from pathlib import Path
from PyQt5 import QtGui, QtCore, QtWidgets, uic
from PyQt5.QtCore import pyqtSignal, Qt, QEvent, QMutex, QTimer, QThread
from PyQt5.QtWidgets import QApplication, QFileDialog
from queue import Queue
from listWidget import ListWidget

import av
import gzip
import json
import math
import numpy as np
import os
import sys
import threading
import time

class Ticker(QThread):
    onTic = pyqtSignal()
    
    def __init__(self, interval=0.050, *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = self.__private = Flexible()
        p.dispose = threading.Event()
        p.disposed = threading.Event()
        p.interval = interval
        
    def run(self):
        p = self.__private
        while not p.dispose.is_set():
            self.onTic.emit()
            try:
                p.dispose.wait(p.interval)
            except Exception:
                pass
            
        p.disposed.set()
        
    def dispose(self):
        p = self.__private
        p.dispose.set()
        
    def join(self):
        p = self.__private
        p.disposed.wait()
    
class GaitMarker(QtWidgets.QMainWindow):
    def load(self, fileId):
        # Load video file by id.
        p = self.__private
        p.mutex.lock()
        p.commands.put(('L', fileId))
        p.nLoads += 1
        p.mutex.unlock()
    
    def seek(self, frameId):
        # Seek frame id.
        p = self.__private
        p.mutex.lock()
        entry = self.getEntry()
        entry['frameId'] = frameId
        p.commands.put(('S', frameId))
        p.nSeeks += 1
        p.mutex.unlock()

    def seekAndHighlight(self, frameId):
        self.seek(frameId)
        self.highlightTemporalLabelsList()
    
    def __thread(self):
        # Load and seek without race conditions (keyPressEvent and processEvents).
        p = self.__private
        p.mutex.lock()
        if p.nLoads > 0:
            # Latest load dismisses any other previous command.
            while p.nLoads > 0:
                command, fileId = p.commands.get()
                if command == 'L':
                    p.nLoads -= 1
                elif command == 'S':
                    p.nSeeks -= 1
            entry = self.getEntry(fileId=fileId)
            frameId = entry['frameId']
            # Cancel seeking task, if any.
            p.cancel.set()
            self.__load(fileId=fileId)
            self.__seek(frameId=frameId)
            self.populateTemporalLabelsList() # !!
            self.highlightTemporalLabelsList()
            self.highlightPointLabelsList()
        elif p.nSeeks > 0:
            # Latest seek replaces any previous seek commands.
            p.cancel.set()
            while p.nSeeks > 0:
                command, frameId = p.commands.get()
                p.nSeeks -= 1
            self.__seek(frameId=frameId)
            self.highlightTemporalLabelsList()
            self.highlightPointLabelsList() # !!
        p.mutex.unlock()
    
        
    def __load(self, fileId):
        # Load video file by id.
        p = self.__private
        videoPath = self.getPath(fileId)
        
        # Release if already opened.
        if p.stream is not None and p.stream.is_open:
            p.stream.close()
            p.container.close()
        
        p.decodedFrame = None
        p.stream = None
        p.nFrames = 0
        p.frameStep = 0
        
        if videoPath.is_file():
            p.container = av.open(str(videoPath))
            p.stream = p.container.streams.video[0]
            p.nFrames = p.stream.frames
            p.fps = float(p.stream.average_rate)
            p.frameStep = round(p.fps * p.secondStep)
    
    def __seek(self, frameId):
        # Seek frame in video stream.
        p = self.__private
        
        p.cancel.clear()
        success = False
        frameId = max(1, round(frameId))
        if frameId == p.decodedFrame:
            success = True
        else:
            entry = self.getEntry()
            if p.stream is not None:
                if 'keyframes' not in entry:
                    entry['keyframes'] = []
                
                # Seek when target frame is behind, or when target is ahead of any previously decoded frame, or when target frame has moved to another keyframe group. Otherwise only decode.
                frameBracket = bisect(entry['keyframes'], frameId)
                # Land one frame before so that the target is decoded next. Also, mind the 1-index.
                timestamp = max(0, math.floor(((frameId - 2) * av.time_base) / p.fps))
                if p.decodedFrame is None or frameId < p.decodedFrame or frameBracket == len(entry['keyframes']) or bisect(entry['keyframes'], p.decodedFrame) != frameBracket:
                    p.container.seek(timestamp)
                
                if p.decodedFrame is None or frameId < p.decodedFrame:
                    seeking = True
                    p.demuxer = p.container.demux(p.stream)
                else:
                    seeking = False
                
                liveUpdates = p.decodedFrame is not None and frameId > p.decodedFrame
                keyframesFound = 2 if frameBracket < len(entry['keyframes']) else 0
                decoding = True
                found = False
                p.enforcedCount = 50
                decodeStart = time.time()
                # Approach target frame.
                while decoding:
                    try:
                        packet = next(p.demuxer)
                        decoding = packet.size > 0
                    except StopIteration:
                        decoding = False
                    if decoding:
                        for frame in packet.decode():
                            # Estimate current frame position.
                            p.decodedFrame = round((frame.pts - p.stream.start_time) * p.stream.time_base * p.fps + 1)
                            # Remember keyframes.
                            if packet.is_keyframe:
                                keyframesFound += 1
                                if p.decodedFrame not in entry['keyframes']:
                                    k = bisect(entry['keyframes'], p.decodedFrame)
                                    entry['keyframes'].insert(k, p.decodedFrame)
                            # Get image at frame when reached or else when canceled.
                            if p.decodedFrame == frameId:
                                image = frame.to_ndarray(format='rgb24')
                                found = True
                                success = True
                            elif not found:
                                if p.cancel.is_set():
                                    image = frame.to_ndarray(format='rgb24')
                                    frameId = p.decodedFrame
                                    success = True
                                elif p.decodedFrame > frameId:
                                    # Demuxer overshot. Try again.
                                    p.container.seek(timestamp)
                                elif not seeking and p.decodedFrame < frameId and (time.time() - decodeStart) * p.fps >= p.enforcedCount:
                                    p.container.seek(timestamp)
                                    seeking = True
                                # Report current frame except when scanning ahead.
                                if liveUpdates:
                                    text = self.updateStatus(p.decodedFrame)
                                    p.ui.fileLabel.setText(text)
                                    p.ui.fileLabel.setText(text)
                            # Stop decoding if canceled, or if target is found and already scanned ahead.
                            if p.cancel.is_set() or (found and keyframesFound >= 2):
                                decoding = False
                                break
            
            if success:
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
            p.drawingBoard.setPoints(x, y, c)
            p.drawingBoard.setImage(image)
            text = self.updateStatus(frameId)
            p.ui.fileLabel.setText(text)
    
    def getPath(self, fileId):
        p = self.__private
        entry = self.getEntry(fileId)
        videoPath = Path(entry['path'])
        if not videoPath.is_absolute():
            videoPath = Path(p.folder) / videoPath
        return videoPath
        
    def getEntry(self, fileId=None):
        # Return current entry.
        p = self.__private
        if fileId is None:
            fileId = p.fileId
        return p.data['entries'][fileId]
            
    def frameId2labelId(self, frameId=None):
        # Find label corresponding to current frame.
        p = self.__private
        entry = self.getEntry()
        if frameId is None:
            frameId = entry['frameId']
        k = bisect_right(entry['events']['frames'], frameId) - 1 if 'events' in entry else -1
        return k
        
    def editEvent(self, previousFrameId, newFrameId):
        p = self.__private
        entry = self.getEntry()
        k = self.frameId2labelId(previousFrameId)
        if k >= 0:
            frames = entry['events']['frames']
            frames[k] = newFrameId
            pairs = sorted(zip(entry['events']['frames'], entry['events']['labels']))
            for index, pair in enumerate(pairs):
                entry['events']['frames'][index], entry['events']['labels'][index] = pair
            probability = entry['events']['p'][k] if 'p' in entry['events'] else None
            p.info[k] = self.style(frames[k], frames[-1], probability)
            p.ui.timeLabelsList.refresh(k)
        
    def relativeSeek(self, step, drag=False):
        # Move step.
        p = self.__private
        entry = self.getEntry()
        newFrameId = min(entry['frameId'] + step, p.nFrames) if step > 0 else max(entry['frameId'] + step, 1)
        success = True
        if drag:
            if newFrameId in entry['events']['frames']:
                success = False
            else:
                self.editEvent(entry['frameId'], newFrameId)
        if success:
            self.seekAndHighlight(frameId=newFrameId)
            # p.ui.timeLabelsList.refresh()
    
    def rotateLoad(self, forward=True):
        p = self.__private
        step = 1 if forward else -1
        scanned = 0
        while scanned < p.nPaths:
            scanned += 1
            p.fileId = (p.fileId + step) % p.nPaths
            videoPath = self.getPath(p.fileId)
            if videoPath.is_file():
                self.load(p.fileId)
                # Enable tools according to availability of parameters in input file.
                entry = self.getEntry()
                visible = 'events' in entry and 'p' in entry['events']
                p.ui.thresholdSpinBox.setEnabled(visible)
                p.ui.thresholdTypeButton.setEnabled(visible)
                break
    
    def rotateTemporalList(self, forward, match=False):
        p = self.__private
        # Move to next/previous event annotation.
        entry = self.getEntry()
        if forward:
            if 'events' in entry:
                frames = entry['events']['frames']
                k = bisect_right(frames, entry['frameId'])
                k = (k - 0) % len(frames)
                if match:
                    labels = entry['events']['labels']
                    currentK = (k - 1) % len(frames)
                    currentLabel = labels[currentK]
                    if 'p' in entry['events']:
                        probabilities = entry['events']['p']
                        threshold = p.ui.thresholdSpinBox.value()
                        above = p.ui.thresholdTypeButton.isChecked()
                        # Make sure that the returned items match the target label and the probability requirements.
                        predicate = lambda label, probability: label == currentLabel and (probability >= threshold if above else probability <= threshold)
                        # List of items satisfying the search parameters.
                        available = lambda : (i for i, (label, probability) in enumerate(zip(labels, probabilities)) if predicate(label, probability))
                        k = next((i for i in available() if i > currentK), next(available(), currentK))
                    else:
                        # List of items satisfying the search parameters.
                        available = lambda : (i for i, label in enumerate(labels) if label == currentLabel)
                        k = next((i for i in available() if i > currentK), next(available(), currentK))
                self.seekAndHighlight(frameId=frames[k])
        else:
            if 'events' in entry:
                frames = entry['events']['frames']
                k = bisect_left(frames, entry['frameId'])
                k = (k - 1) % len(frames)
                if match:
                    labels = entry['events']['labels']
                    currentK = (k + 1) % len(frames)
                    currentLabel = labels[currentK]
                    n = len(frames)
                    if 'p' in entry['events']:
                        probabilities = entry['events']['p']
                        threshold = p.ui.thresholdSpinBox.value()
                        above = p.ui.thresholdTypeButton.isChecked()
                        # Make sure that the returned items match the target label and the probability requirements.
                        predicate = lambda label, probability: label == currentLabel and (probability >= threshold if above else probability <= threshold)
                        # List of items satisfying the search parameters.
                        available = lambda : (n - i - 1 for i, (label, probability) in enumerate(zip(reversed(labels), reversed(probabilities))) if predicate(label, probability))
                        k = next((i for i in available() if i < currentK), next(available(), currentK))
                    else:
                        # List of items satisfying the search parameters.
                        available = lambda : (n - i - 1 for i, label in enumerate(reversed(labels)) if label == currentLabel)
                        k = next((i for i in available() if i < currentK), next(available(), currentK))
                self.seekAndHighlight(frameId=frames[k])
    
    def rotatePointAnnotation(self, forward):
        # Move to next/previous point annotation.
        entry = self.getEntry()
        if 'points' in entry:
            if forward:
                points = entry['points']
                k = bisect_right(points['frames'], entry['frameId'])
                k = k % len(points['frames'])
                self.seek(frameId=points['frames'][k])
            else:
                points = entry['points']
                k = bisect_left(points['frames'], entry['frameId']) - 1
                k = k % len(points['frames'])
                self.seek(frameId=points['frames'][k])
                
    def rotatePointLabelList(self, forward):
        p = self.__private
        if len(p.data['labels']) > 0:
            delta = 1 if forward else -1
            p.pointLabel = (p.pointLabel + delta) % len(p.data['labels'])
            self.highlightPointLabelsList()
    
    def insertTemporalLabel(self):
        # Add a temporal label.
        p = self.__private
        entry = self.getEntry()
        if 'events' not in entry:
            entry['events'] = {
                'frames': [entry['frameId']],
                'labels': ['']
            }
            k = 0
        elif entry['frameId'] not in entry['events']['frames']:
            k = bisect_right(entry['events']['frames'], entry['frameId'])
            entry['events']['frames'].insert(k, entry['frameId'])
            entry['events']['labels'].insert(k, '')
            if 'p' in entry['events']:
                entry['events']['p'].insert(k, 1)
        else:
            k = -1
        if k >= 0:
            self.populateTemporalLabelsList()
            # Enter edit mode.
            p.ui.timeLabelsList.startEditing(k)
    
    def removeTemporalLabel(self):
        # Remove current temporal label.
        p = self.__private
        entry = self.getEntry()
        if 'events' in entry:
            if len(entry['events']['frames']) == 1:
                print(f"Deleted {entry['events']['frames'][0]}")
                p.ui.timeLabelsList.clear()
                del entry['events']
                p.info.clear()
                self.populateTemporalLabelsList() # !!
            else:
                k = self.frameId2labelId()
                print(f"Deleted {entry['events']['frames'][k]}")
                del p.info[k]
                del entry['events']['frames'][k]
                del entry['events']['labels'][k]
                if 'p' in entry['events']:
                    del entry['events']['p'][k]
                self.populateTemporalLabelsList() # ==> Slow given that only one element is being deleted.
                self.seekAndHighlight(frameId=entry['events']['frames'][k - 1 if k == len(entry['events']['frames']) else k])
    
    def updateStatus(self, frameId=None):
        p = self.__private
        if p.nPaths > 0:
            entry = self.getEntry()
            if frameId is None:
                frameId = entry['frameId']
            text = f"[{p.fileId + 1}:{p.nPaths} / {frameId}:{p.nFrames}] {entry['path']}"
        else:
            text = '[0:0 / 0:0] No files found.' % str(Path(p.folder))
        return text
        
    def populateTemporalLabelsList(self):
        # Update temporal label list.
        p = self.__private
        if p.nPaths > 0:
            entry = self.getEntry()
            if 'events' in entry:
                frames = entry['events']['frames']
                labels = entry['events']['labels']
                if 'p' in entry['events']:
                    probabilities = entry['events']['p']
                    p.info = [self.style(frames[i], frames[-1], probabilities[i]) for i in range(len(frames))]
                else:
                    p.info = [self.style(frames[i], frames[-1]) for i in range(len(frames))]
                table = (p.info, labels)
                p.ui.timeLabelsList.setData(table)
            else:
                p.ui.timeLabelsList.clear()

    def style(self, frame, maxFrame, probability=None):
        n = math.floor(math.log10(maxFrame)) + 1
        if probability:
            output = f'{frame:0{n}d} {probability:.2f} | '
        else:
            output = f'{frame:0{n}d} │ '
        return output
    
    def highlightPointLabelsList(self):
        # Update spatial label.
        p = self.__private
        # Spatial annotations.
        p.ui.pointLabelsList.blockSignals(True)
        if len(p.data['labels']) > 0:
            index = p.pointLabel % len(p.data['labels'])
            p.ui.pointLabelsList.setCurrentRow(index)
        p.ui.pointLabelsList.blockSignals(False)
        
    def highlightTemporalLabelsList(self):
        # Highlight current temporal label.
        p = self.__private
        entry = self.getEntry()
        frameId = entry['frameId']
        k = self.frameId2labelId(frameId)
        p.ui.timeLabelsList.select(k)
    
    def keyPressEvent(self, event):
        # Process key presses.
        p = self.__private
        enter = event.key() in (QtCore.Qt.Key_Return, Qt.Key_Enter)
        alt = modifier(event, QtCore.Qt.AltModifier)
        shift = modifier(event, QtCore.Qt.ShiftModifier)
        control = modifier(event, QtCore.Qt.ControlModifier)
        entry = self.getEntry()
        if event.key() == Qt.Key_S:
            # Save annotation data.
            if control:
                self.save()
        elif event.key() in (Qt.Key_Down, Qt.Key_Up):
            forward = event.key() == Qt.Key_Up
            if control:
                # Load previous video file.
                self.rotateLoad(forward)
            else:
                self.rotatePointAnnotation(forward)
        elif event.key() in (Qt.Key_Left, Qt.Key_Right):
            forward = event.key() == Qt.Key_Right
            if control:
                self.rotateTemporalList(forward=forward, match=shift)
            else:
                # Advance frame position by a given step.
                step = 1 if shift else p.frameStep
                step *= 1 if forward else -1
                self.relativeSeek(step=step, drag=alt)
        elif enter and 'events' in entry:
            k = self.frameId2labelId()
            if alt:
                # Assign current frame to current temporal label.
                entry['events']['frames'][k] = entry['frameId']
            elif control and 'p' in entry['events']:
                if 'p' in entry['events']:
                    entry['events']['p'][k] = 1.0
                    p.info[k] = self.style(entry['events']['frames'][k], entry['events']['frames'][-1], 1.0)
                    p.ui.timeLabelsList.refresh(k)
        elif event.key() == Qt.Key_Home:
            # Go to first frame.
            entry['frameId'] = 1
            self.seekAndHighlight(frameId=entry['frameId'])
        elif event.key() == Qt.Key_End:
            # Go to last frame.
            entry['frameId'] = p.nFrames
            self.seekAndHighlight(frameId=entry['frameId'])
        elif event.key() == Qt.Key_Insert:
            # Insert event.
            self.insertTemporalLabel()
        elif event.key() == Qt.Key_Delete:
            if shift:
                # Delete a point corresponding to the spatial label selected.
                if 'points' in entry:
                    points = entry['points']
                    k = next((k for k, (frame, label) in enumerate(zip(points['frames'], points['labels'])) if frame == entry['frameId'] and label == p.pointLabel), None)
                    if k is not None:
                        del points['frames'][k]
                        del points['labels'][k]
                        del points['x'][k]
                        del points['y'][k]
                        del points['p'][k]
                    if len(points['frames']) == 0:
                        del entry['points']
                    x = y = c = []
                    if entry['frameId'] in points['frames']:
                        x, y, labels, frames = zip(*filter(lambda z: z[3] == entry['frameId'], zip(points['x'], points['y'], points['labels'], points['frames'])))
                        c = [p.palette[i] for i in labels]
                    p.drawingBoard.setPoints(x, y, c)
            else:
                # Delete event and annotation that follows.
                self.removeTemporalLabel()
        elif event.key() in (Qt.Key_PageDown, Qt.Key_PageUp):
            if len(p.data['labels']) > 0:
                # Load previous video file.
                forward = event.key() == Qt.Key_PageUp
                self.rotateLoad(forward)
        elif event.key() == Qt.Key_Escape:
            p.cancel.set()
        elif event.key() == QtCore.Qt.Key_F2:
            labels = entry['events']['labels']
            k = self.frameId2labelId()
            p.ui.timeLabelsList.startEditing(k)
        super().keyPressEvent(event)
    
    def onWheelEvent(self, event, source):
        # Capture mouse wheel events from given widgets.
        p = self.__private
        alt = modifier(event, QtCore.Qt.AltModifier)
        shift = modifier(event, QtCore.Qt.ShiftModifier)
        control = modifier(event, QtCore.Qt.ControlModifier)
        forward = event.angleDelta().x() <= 0 if alt else event.angleDelta().y() <= 0
        
        if source == p.ui.timeLabelsList:
            if control:
                if not alt:
                    self.rotateTemporalList(forward, shift)
            else:
                step = 1 if shift else p.frameStep
                self.relativeSeek(step=step if forward else -step, drag=alt)
        elif source == p.ui.pointLabelsList:
            if not alt and not shift:
                if control:
                    self.rotatePointAnnotation(forward)
                else:
                    self.rotatePointLabelList(forward)
        super().wheelEvent(event)
    
    def onBoardMouseEvent(self, data):
        # Capture mouse events issued by the drawing board.
        p = self.__private
        event = data.event
        alt = modifier(event, QtCore.Qt.AltModifier)
        shift = modifier(event, QtCore.Qt.ShiftModifier)
        control = modifier(event, QtCore.Qt.ControlModifier)
        if data.category == 'release':
            entry = self.getEntry()
            if 'labels' in p.data and len(p.data['labels']) > 0:
                # Add when left click alone.
                if event.button() == Qt.LeftButton and not shift and not control:
                    if 'points' not in entry:
                        entry['points'] = {'frames':[], 'labels':[], 'x':[], 'y':[], 'p':[]}
                    points = entry['points']
                    if 'p' not in points:
                        points['p'] = [1] * len(points['frames'])
                        
                    k = next((k for k, (frame, label) in enumerate(zip(points['frames'], points['labels'])) if frame == entry['frameId'] and label == p.pointLabel), None)
                    if k is None:
                        k = bisect(points['frames'], entry['frameId'])
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
                p.drawingBoard.setPoints(x, y, c)
        elif data.category == 'wheel':
            forward = event.angleDelta().x() <= 0 if alt else event.angleDelta().y() <= 0
            self.rotatePointLabelList(forward)
    
    def onAddItem(self, combo, text=''):
        # Add spatial or temporal label.
        p = self.__private
        if combo == p.ui.pointLabelsList:
            if text not in p.data['labels']:
                combo.blockSignals(True)
                p.data['labels'].append(text)
                item = QtWidgets.QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                combo.addItem(item)
                QtCore.QCoreApplication.processEvents()
                p.pointLabel = len(p.data['labels']) - 1
                # Enter edit mode.
                combo.editItem(item)
                combo.setCurrentItem(item)
                combo.blockSignals(False)
        elif combo == p.ui.timeLabelsList:
            self.insertTemporalLabel()
    
    def onRemoveItem(self, combo):
        # Remove spatial or temporal label.
        p = self.__private
        if combo == p.ui.pointLabelsList:
            p.ui.pointLabelsList.blockSignals(True)
            if len(p.data['labels']) > 0:
                index = combo.row(combo.currentItem())
                del p.data['labels'][index]
                combo.takeItem(index)
                p.pointLabel = max(index - 1, 0)
                if len(p.data['labels']) > 0:
                    combo.setCurrentRow(p.pointLabel)
            p.ui.pointLabelsList.blockSignals(False)
        elif combo == p.ui.timeLabelsList:
            self.removeTemporalLabel()
    
    def onItemSelectionChanged(self, combo):
        # Update frame shown according to selected spatial label.
        # Action initiated by user.
        p = self.__private
        entry = self.getEntry()
        if combo == p.ui.pointLabelsList:
            p.pointLabel = combo.row(combo.currentItem())
        elif combo == p.ui.timeLabelsList:
            k = combo.selected()
            entry['frameId'] = entry['events']['frames'][k]
            self.seek(frameId=entry['frameId'])
    
    def onEditPointLabelsList(self, text):
        p = self.__private
        p.data['labels'][p.pointLabel] = text
    
    def onEditTimeLabelsList(self, row, text):
        entry = self.getEntry()
        entry['events']['labels'][row] = text

    def closeEvent(self, event):
        # Release video stream, save progress and close.
        p = self.__private
        self.save()
        p.dispose.set()
        p.ticker.dispose()
        p.ticker.join()
        if p.stream is not None:
            p.stream.close()
            p.container.close()
        event.accept()
    
    def save(self):
        # Save progress.
        p = self.__private
        filename = str(p.output)
        # Only save entries with annotations.
        data = dict(p.data)
        data['entries'] = [entry for entry in p.data['entries'] if 'events' in entry or 'points' in entry]
        if filename.lower().endswith('.gz'):
            with gzip.open(filename, 'wt', encoding='utf-8') as file:
                file.write(json.dumps(data))
        else:
            with open(filename, 'w', encoding='utf-8') as file:
                file.write(json.dumps(data, indent=2))
        
    def __init__(self, file, folder='.', videos=[], *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = self.__private = Flexible()
        
        p.stream = None
        p.secondStep = 5.00
        p.pointLabel = 0
        p.info = []
        
        p.dispose = threading.Event()
        p.cancel = threading.Event()
        p.commands = Queue()
        p.mutex = QMutex()
        p.ticker = Ticker()
        p.ticker.onTic.connect(self.__thread)
        p.ticker.start()
        
        p.nLoads = 0
        p.nSeeks = 0
        p.palette = ('#2f4f4f', '#8b4513', '#191970', '#006400', '#ff0000', '#ffa500', '#ffff00', '#00ff00', '#00bfff', '#0000ff', '#ff00ff', '#dda0dd', '#ff1493', '#98fb98', '#ffdead') # !!
        
        p.output = Path(file)
        p.folder = folder = Path(folder)

        if folder.is_dir():
            success = True
            # Create new file or parse contents if an existing one is provided.
            if p.output.is_file():
                try:
                    if p.output.suffix.lower() == '.gz':
                        file = gzip.open(p.output, 'rt', encoding='utf-8')
                    else:
                        file = open(p.output, 'rt', encoding='utf-8')
                    p.data = json.load(file)
                    file.close()
                except Exception as ex:
                    success = False
                    exception = str(ex)
                    print('Could not load data from "%s" ==> %s' % (p.output.as_posix(), exception))
            else:
                p.data = {'labels':[], 'entries':[]}
            
            if success:
                # Make paths relative to folder when possible.
                paths = list([entry['path'] for entry in p.data['entries']])
                paths = relative(folder, paths)
                for path, entry in zip(paths, p.data['entries']):
                    entry['path'] = path
                videos = relative(folder, videos)
                
                # Maintain existing data.
                difference = list(set(videos).difference(paths))
                paths.extend(difference)
                for path in difference:
                    p.data['entries'].append({'path': path})
                
                # Pre-process file.
                for entry in p.data['entries']:
                    if 'frameId' not in entry:
                        entry['frameId'] = 1
                
                # Initialize.
                p.nPaths = len(paths)
                if p.nPaths == 0:
                    success = False
                    print('No files available')
                    p.ui = uic.loadUi('UI.ui', self)
                    QtCore.QMetaObject.invokeMethod(self, 'close', Qt.QueuedConnection)
                else:
                    bundleDir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
                    p.ui = uic.loadUi(bundleDir / 'UI.ui', self)
                    # Setup pointLabelsList with editable elements.
                    if 'labels' not in p.data:
                        p.data['labels'] = []
                    for label in list(p.data['labels']):
                        item = QtWidgets.QListWidgetItem(label)
                        item.setFlags(item.flags() | Qt.ItemIsEditable)
                        p.ui.pointLabelsList.addItem(item)
                    p.ui.pointLabelsList.itemChanged.connect(lambda item: self.onEditPointLabelsList(item.text()))
                    p.ui.pointLabelsList.setCurrentRow(0)
                    p.ui.pointLabelsList.itemSelectionChanged.connect(lambda : self.onItemSelectionChanged(p.ui.pointLabelsList))
                    p.ui.pointLabelsList.wheelEvent = lambda event: self.onWheelEvent(event, p.ui.pointLabelsList)
                    p.ui.addSpatialButton.clicked.connect(lambda checked : self.onAddItem(combo=p.ui.pointLabelsList, text=''))
                    p.ui.removeSpatialButton.clicked.connect(lambda checked : self.onRemoveItem(combo=p.ui.pointLabelsList))
                    # Setup timeLabelsList.
                    p.ui.timeLabelsList = ListWidget(parent=p.ui.TemporalGUI)
                    p.ui.timeLabelsList.setFocusPolicy(Qt.NoFocus)
                    p.ui.temporalLayout.insertWidget(0, p.ui.timeLabelsList)
                    p.ui.timeLabelsList.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.MinimumExpanding)
                    p.ui.timeLabelsList.onValueChange.connect(lambda widget, row, text: self.onEditTimeLabelsList(row, text))
                    p.ui.timeLabelsList.onSelectionChanged.connect(lambda : self.onItemSelectionChanged(p.ui.timeLabelsList))
                    p.ui.timeLabelsList.wheelEvent = lambda event : self.onWheelEvent(event, p.ui.timeLabelsList)
                    p.ui.addTimeButton.clicked.connect(lambda checked : self.onAddItem(combo=p.ui.timeLabelsList, text=''))
                    p.ui.removeTimeButton.clicked.connect(lambda checked : self.onRemoveItem(combo=p.ui.timeLabelsList))
                    # Setup threshold GUI.
                    p.ui.thresholdTypeButton.toggled.connect(lambda state : p.ui.thresholdTypeButton.setText('Threshold: ≥' if state else 'Threshold: ≤'))

                    p.drawingBoard = DrawingBoard(self)
                    p.drawingBoard.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.MinimumExpanding)
                    p.drawingBoard.setMinimumSize(QtCore.QSize(0, self.height() // 2))
                    p.drawingBoard.mouse.connect(self.onBoardMouseEvent)
                    p.ui.WindowLayout.insertWidget(0, p.drawingBoard)
                    self.setWindowTitle('Gait Marker')
                    self.show()
                    p.fileId = -1
                    self.rotateLoad()
        else:
            print('Project folder "%s" not found.' % folder.as_posix())
            app = QApplication([])
            app.exit()
        

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
        
class DrawingBoard(QtWidgets.QWidget):
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
        
    def wheelEvent(self, event):
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

def relative(folder, paths):
    # Get path relative to folder.
    for index, path in enumerate(paths):
        path = Path(path)
        try:
            paths[index] = path.relative_to(folder).as_posix()
        except:
            paths[index] = path.as_posix()
    return paths
    
def modifier(event, modifier):
    return (event.modifiers() & modifier) == modifier
        
if __name__ == '__main__':
    print(f'GaitMarker v{version}')
    print(f'©Leo Molina 2021')
    
    # Factory settings.
    debug = False
    settingsPath = Path.home() / "GaitMarkerSettings.json"
    projectFolder = Path.home() / "Documents"
    projectFile = projectFolder / 'project.json.gz'
    extensions = ('.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.mpeg', '.mpg', '.h264')

    # Prompt last project folder and project file if available.
    settings = dict(folder=str(projectFolder), file=str(projectFile))
    if settingsPath.is_file():
        try:
            file = open(str(settingsPath), 'rt', encoding='utf-8')
            loaded = json.load(file)
            file.close()
            success = True
        except Exception as ex:
            success = False
            exception = str(ex)
            print('Could not load data from "%s" ==> %s' % (settingsPath, exception))
        if success:
            for setting in settings.keys():
                settings[setting] = loaded[setting]
                
    # Select project folder and project file.
    run = False
    app = QApplication([])
    options = QFileDialog.Options()
    options |= QFileDialog.DontConfirmOverwrite
    projectFile = settings['file'] if debug else QFileDialog.getSaveFileName(parent=None, caption='Select an existing project file to continue or type a new name to start from scratch', directory=settings['file'], filter='JSON compressed file (*.json.gz);; JSON file (*.json);; All supported files (*.json.gz *.json)', initialFilter='All supported files (*.json.gz *.json)', options=options)[0]
    if len(projectFile) > 0:
        settings['file'] = projectFile
        projectFolder = settings['folder'] if debug else QFileDialog.getExistingDirectory(None, 'Select project folder', settings['folder'], QtWidgets.QFileDialog.ShowDirsOnly)
        if len(projectFolder) > 0:
            settings['folder'] = projectFolder
            run = True
        
        # Save settings.
        with open(str(settingsPath), 'w', encoding='utf-8') as file:
            file.write(json.dumps(settings, indent=2))
    
    if run:
        # Search video files within project folder.
        videoList = []
        for file in Path(projectFolder).rglob("*"):
            if file.suffix.lower() in extensions:
                videoList.append(file.as_posix())
        
        # Launch app.
        GaitMarker(file=projectFile, folder=projectFolder, videos=videoList)
        exitCode = app.exec()
    else:
        exitCode = 0
    
    sys.exit(exitCode)