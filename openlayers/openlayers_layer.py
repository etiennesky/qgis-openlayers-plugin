# -*- coding: utf-8 -*-
"""
/***************************************************************************
OpenLayers Plugin
A QGIS plugin

                             -------------------
begin                : 2010-02-03
copyright            : (C) 2010 by Pirmin Kalberer, Sourcepole
email                : pka at sourcepole.ch
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import PyQt4
from PyQt4.QtCore import *
from PyQt4.QtGui import *
from PyQt4.QtWebKit import *
from PyQt4.QtNetwork import *
from qgis.core import *

from tools_network import getProxy

import os.path
import math
import sys

debuglevel = 4  # 0 (none) - 4 (all)


def debug(msg, verbosity=1):
    if debuglevel >= verbosity:
        qDebug(msg)


class OLWebPage(QWebPage):
    def __init__(self, parent=None):
        QWebPage.__init__(self, parent)
        self.__manager = None  # Need persist for PROXY
        # Set Proxy in webpage
        proxy = getProxy()
        if not proxy is None:
            self.__manager = QNetworkAccessManager()
            self.__manager.setProxy(proxy)
            self.setNetworkAccessManager(self.__manager)

    def javaScriptConsoleMessage(self, message, lineNumber, sourceID):
        qDebug("%s[%d]: %s" % (sourceID, lineNumber, message))


# this is a worker class which is responsible for fetching the map
# it resides normally in the gui thread, except when doind paint()
class OpenlayersController(QObject):

    # signal that reports to the worker thread that the image is ready
    finished = pyqtSignal()

    def __init__(self, rendererContext, mapSettings, layerType):
        QObject.__init__(self)

        # img vars
        self.viewportSize = QSize(400, 400)
        self.img = QImage(self.viewportSize, QImage.Format_ARGB32)
        #self.img = None

        #web page vars
        self.page = OLWebPage()
        #self.page.setViewportSize(self.viewportSize)
        self.page.loadFinished.connect(self.pageFinished)

        # specific vars
        self.rendererContext = rendererContext
        self.mapSettings = mapSettings
        self.layerType = layerType

        self.loaded = False
        self.ext = None
        self.olResolutions = None

        self.lastRenderedImage = None
        self.lastExtent = None
        self.lastViewPortSize = None
        self.lastLogicalDpi = None
        self.lastOutputDpi = None
        self.lastMapUnitsPerPixel = None

        # timeout for loadEnd event
        self.timerLoadEnd = QTimer()
        self.timerLoadEnd.setSingleShot(True)
        self.timerLoadEnd.setInterval(5000)
        QObject.connect(self.timerLoadEnd, SIGNAL("timeout()"), self.loadEndTimeout)


    @pyqtSlot()
    def request(self):
        sys.stderr.write("[GUI THREAD] Processing request\n")
        self.cancelled = False
        #url = QUrl("http://qgis.org/")
        #url = QUrl(os.path.join(os.path.dirname(__file__), "testpage.html"))
        #self.page.mainFrame().load(url)

        debug("OpenlayersController request " + str(QThread.currentThreadId()))

        olSize = self.rendererContext.painter().viewport().size()
        self.page.setViewportSize(olSize)

        #self.page = OLWebPage()
        url = self.layerType.html_url()
        debug("page file: %s" % url)
        self.page.mainFrame().load(QUrl(url))


    def waitForLoadEnd(self):
        if self.layerType.emitsLoadEnd:
            debug('waiting for loadEnd', 3)
            # wait for OpenLayers to finish loading
            # NOTE: does not work with Google and Yahoo layers as they do not emit loadstart and loadend events
            self.loadEnd = False
            self.timerLoadEnd.start()
            while not self.loadEnd:
                loadEndOL = self.page.mainFrame().evaluateJavaScript("loadEnd")
                #debug('waiting ' + str(rendererContext.renderingStopped()) + ' ' + str(loadEndOL), 4)
                #debug("loadEndOL: %d" % loadEndOL, 3)
                #if not loadEndOL.isNull():
                if not loadEndOL is None:
                    self.loadEnd = loadEndOL
                else:
                    debug("OpenlayersLayer Warning: Could not get loadEnd")
                    break
                qApp.processEvents()
            self.timerLoadEnd.stop()
            debug('done waiting for loadEnd', 3)
        else:
            debug('waiting for pageRepaintRequested', 3)
                # wait for timeout after pageRepaintRequested
            self.repaintEnd = False
            self.timerMax.start()
            while not self.repaintEnd:
                qApp.processEvents()
            self.timerMax.stop()
            debug('done waiting for pageRepaintRequested', 3)

    def cancel(self):
        self.cancelled = True
        self.timerLoadEnd.stop()
        
    def loadEndTimeout(self):
        debug("OpenlayersLayer loadEndTimeout")
        self.loadEnd = True

    def pageFinished(self):
        print('pageFinished')

        sys.stderr.write("[GUI THREAD] Request finished\n")

        if self.cancelled:
            self.img = None
            self.finished.emit()
            return
        
        #self.waitForLoadEnd()

        rendererContext = self.rendererContext

        outputDpi = self.mapSettings.outputDpi()
        debug(" extent: %s" % rendererContext.extent().toString(), 3)
        debug(" center: %lf, %lf" % (rendererContext.extent().center().x(), rendererContext.extent().center().y()), 3)
        debug(" size: %d, %d" % (rendererContext.painter().viewport().size().width(), rendererContext.painter().viewport().size().height()), 3)
        debug(" logicalDpiX: %d" % rendererContext.painter().device().logicalDpiX(), 3)
        debug(" outputDpi: %lf" % outputDpi)
        debug(" mapUnitsPerPixel: %f" % rendererContext.mapToPixel().mapUnitsPerPixel(), 3)
        #debug(" rasterScaleFactor: %s" % str(rendererContext.rasterScaleFactor()), 3)
        #debug(" outputSize: %d, %d" % (self.iface.mapCanvas().mapRenderer().outputSize().width(), self.iface.mapCanvas().mapRenderer().outputSize().height()), 3)
        #debug(" scale: %lf" % self.iface.mapCanvas().mapRenderer().scale(), 3)

        painter_saved = False

        if self.lastExtent != rendererContext.extent() or self.lastViewPortSize != rendererContext.painter().viewport().size() or self.lastLogicalDpi != rendererContext.painter().device().logicalDpiX() or self.lastOutputDpi != outputDpi or self.lastMapUnitsPerPixel != rendererContext.mapToPixel().mapUnitsPerPixel():
            olSize = rendererContext.painter().viewport().size()
            if rendererContext.painter().device().logicalDpiX() != int(outputDpi):
                # use screen dpi for printing
                #sizeFact = outputDpi / 25.4 / rendererContext.mapToPixel().mapUnitsPerPixel()
                sizeFact = 1
                olSize.setWidth(rendererContext.extent().width() * sizeFact)
                olSize.setHeight(rendererContext.extent().height() * sizeFact)
            debug(" olSize: %d, %d" % (olSize.width(), olSize.height()), 3)
            self.page.setViewportSize(olSize)
            targetWidth = olSize.width()
            targetHeight = olSize.height()

            # find best resolution or use last
            qgisRes = rendererContext.extent().width() / targetWidth
            for res in self.resolutions():
                olRes = res
                if qgisRes >= res:
                    break

            # adjust OpenLayers viewport to match QGIS extent
            olWidth = rendererContext.extent().width() / olRes
            olHeight = rendererContext.extent().height() / olRes
            debug("    adjust viewport: %f -> %f: %f x %f" % (qgisRes, olRes, olWidth, olHeight), 3)
            self.page.setViewportSize(QSize(olWidth, olHeight))

            if rendererContext.extent() != self.ext:
                self.ext = rendererContext.extent()  # FIXME: store seperate for each rendererContext
                debug("updating OpenLayers extent (%f, %f, %f, %f)" % (self.ext.xMinimum(), self.ext.yMinimum(), self.ext.xMaximum(), self.ext.yMaximum()), 3)
                bla = self.page.mainFrame().evaluateJavaScript(
                    "map.zoomToExtent(new OpenLayers.Bounds(%f, %f, %f, %f), true);" %
                    (self.ext.xMinimum(), self.ext.yMinimum(), self.ext.xMaximum(), self.ext.yMaximum()))
                print(str(bla))
                debug("map.zoomToExtent finished", 3)

            self.waitForLoadEnd()

            if self.cancelled:
                self.img = None
                self.finished.emit()
                return

            debug("OpenlayersController rendering page to img", 3)

            #Render WebKit page into rendererContext
            #debug('before save', 4)
            #rendererContext.painter().save()
            #debug('after save', 4)
            #painter_saved = True
            if rendererContext.painter().device().logicalDpiX() != int(outputDpi):
                printScale = 25.4 / outputDpi  # OL DPI to printer pixels
                rendererContext.painter().scale(printScale, printScale)

            # render OpenLayers to image
            img = QImage(olWidth, olHeight, QImage.Format_ARGB32_Premultiplied)
            painter = QPainter(img)
            self.page.mainFrame().render(painter)
            painter.end()
            img.save("/tmp/openlayers.png")

            if olWidth != targetWidth or olHeight != targetHeight:
                # scale using QImage for better quality
                img = img.scaled(targetWidth, targetHeight, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                debug("    scale image: %i x %i -> %i x %i" % (olWidth, olHeight, targetWidth, targetHeight), 3)

        else:
            debug("OpenlayersController using cached img", 3)
            img = self.lastRenderedImage

#        debug("OpenlayersController rendering img to painter", 3)

#        # draw to rendererContext
#        rendererContext.painter().drawImage(0, 0, img)
#        if painter_saved:
#            rendererContext.painter().restore()

#        debug("OpenlayersController done rendering img to painter", 3)

        # save current state
        self.lastRenderedImage = img
        self.img = img
        self.lastExtent = rendererContext.extent()
        self.lastViewPortSize = rendererContext.painter().viewport().size()
        self.lastLogicalDpi = rendererContext.painter().device().logicalDpiX()
        self.lastOutputDpi = self.mapSettings.outputDpi()
        self.lastMapUnitsPerPixel = rendererContext.mapToPixel().mapUnitsPerPixel()

        self.finished.emit()

        debug("OpenlayersController render done")


    #def scaleFromExtent(self, extent):

    def resolutions(self):
        if self.olResolutions is None:
            # get OpenLayers resolutions
            resVariant = self.page.mainFrame().evaluateJavaScript("map.layers[0].resolutions")
            self.olResolutions = resVariant
            #for res in resVariant.toList():
            #    self.olResolutions.append(res.toDouble()[0])
        return self.olResolutions


# this is a map renderer which resides in a worker thread, and uses a OpenlayersController
# which resides in the gui thread to do fetch the map
class OpenlayersRenderer(QgsMapLayerRenderer):

    def __init__(self, layer, context, mapSettings, layerType):

        QgsMapLayerRenderer.__init__(self, layer.id())

        debug('OpenlayersRenderer __init__ ' + self.layerID() + ' - ' + str(QThread.currentThreadId()))

        self.context = context
        self.controller = OpenlayersController(context, mapSettings, layerType)
        self.loop = None

    def render(self):
        """ do the rendering. This function is called in the worker thread """

        sys.stderr.write("[WORKER THREAD] Calling request() asynchronously\n")
        QMetaObject.invokeMethod(self.controller, "request")

        # setup a timer that checks whether the rendering has not been stopped in the meanwhile
        timer = QTimer()
        timer.setInterval(50)
        timer.timeout.connect(self.onTimeout)
        timer.start()

        sys.stderr.write("[WORKER THREAD] Waiting for the async request to complete\n")
        self.loop = QEventLoop()
        self.controller.finished.connect(self.loop.exit)
        self.loop.exec_()

        sys.stderr.write("[WORKER THREAD] Async request finished\n")

        if self.controller.img:
            sys.stderr.write("[WORKER THREAD] drawing controller image\n")
            painter = self.context.painter()
            painter.drawImage(0, 0, self.controller.img)
        else:
            sys.stderr.write("[WORKER THREAD] no controller image to draw\n")

        return True

    def onTimeout(self):
        """ periodically check whether the rendering should not be stopped """
        if self.context.renderingStopped():
            sys.stderr.write("[WORKER THREAD] Cancelling rendering\n")
            self.controller.cancel()
            self.loop.exit()


class OpenlayersLayer(QgsPluginLayer):

    LAYER_TYPE = "openlayers"
    MAX_ZOOM_LEVEL = 15
    SCALE_ON_MAX_ZOOM = 13540  # QGIS scale for 72 dpi

    def __init__(self, iface, olLayerTypeRegistry):
        debug("OpenlayersLayer init - " + str(QThread.currentThreadId()))
        QgsPluginLayer.__init__(self, OpenlayersLayer.LAYER_TYPE, "OpenLayers plugin layer")
        self.setValid(True)
        self.olLayerTypeRegistry = olLayerTypeRegistry

        self.iface = iface

        #Set default layer type
        self.setLayerType(self.olLayerTypeRegistry.getById(0))

        self.mapRenderer = None

    #def draw(self, rendererContext):
    #    debug("OpenlayersLayer draw "+str(QThread.currentThreadId ()))
    #    return True

    def readXml(self, node):
        # custom properties
        self.setLayerType(self.olLayerTypeRegistry.getById(int(node.toElement().attribute("ol_layer_type", "0"))))
        return True

    def writeXml(self, node, doc):
        element = node.toElement()
        # write plugin layer type to project (essential to be read from project)
        element.setAttribute("type", "plugin")
        element.setAttribute("name", OpenlayersLayer.LAYER_TYPE)
        # custom properties
        element.setAttribute("ol_layer_type", str(self.layerType.id))
        return True

    def setLayerType(self, layerType):
        self.layerType = layerType
        coordRefSys = self.layerType.coordRefSys(None)  # FIXME
        self.setCrs(coordRefSys)
        #TODO: get extent from layer type
        self.setExtent(QgsRectangle(-20037508.34, -20037508.34, 20037508.34, 20037508.34))

    def createMapRenderer(self, rendererContext):
        debug('OpenLayers createMapRenderer ' + str(QThread.currentThreadId()) + ' - ' + str(rendererContext.mapToPixel().mapUnitsPerPixel()), 3)
        #self.worker.rendererContext = rendererContext
        #self.mapRenderer = OpenlayersRenderer(self.id(), self.worker)#, rendererContext, self.iface.mapCanvas().mapSettings(), self.layerType)
        self.mapRenderer = OpenlayersRenderer(self, rendererContext, self.iface.mapCanvas().mapSettings(), self.layerType)
        return self.mapRenderer
