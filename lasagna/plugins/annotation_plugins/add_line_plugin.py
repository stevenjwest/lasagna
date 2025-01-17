"""lasagna.plugins.annotation_plugins.add_line_plugin

    This Lasagna plugin is used to annotate image stacks by allowing the user to
    add lines and points interactively to the image stack.



    For hints on plugin writing see the tutorial plugins.

    Author
    Antonin Blot, Basel
"""

import numpy as np
from PyQt5 import QtGui, QtWidgets
import scipy.linalg  # For the 3D line fit

from lasagna.plugins.lasagna_plugin import LasagnaPlugin
from lasagna.plugins.annotation_plugins import add_line_UI


class plugin(LasagnaPlugin, QtWidgets.QWidget, add_line_UI.Ui_addLine):
    def __init__(self, lasagna_serving, parent=None):
        super(plugin, self).__init__(lasagna_serving)

        # re-define some default properties that were originally defined in LasagnaPlugin
        self.pluginShortName = "Add lines"  # Appears on the menu
        self.pluginLongName = "manually add lines and points"
        self.pluginAuthor = "Antonin Blot"

        # Create widgets defined in the designer file
        self.setupUi(self)
        self.show()
        self.name_lineEdit.setText("ManualLine")

        # Set up the close button by linking it to the same slot as the normal window close button
        self.closeButton.released.connect(self.closePlugin)
        self.num_points = 0  # The number of points added see self.hook_axisClicked()
        self.numPoints_textLabel.setText("n pts: %d" % self.num_points)

        # -- Add ingredients to Lasagna

        # 1. Add a sparsepoints ingredient for the clicked points
        self.pts_name = "addLine_currentLine"
        self.lasagna.addIngredient(
            objectName=self.pts_name, kind="sparsepoints", data=[]
        )

        self.lasagna.returnIngredientByName(self.pts_name).addToPlots()
        self.lasagna.returnIngredientByName(self.pts_name).color = [42, 96, 255]
        self.lasagna.returnIngredientByName(self.pts_name).symbolSize = 8

        # 2. Add a line ingredient for the line linking or fitting the points
        self.line_name = "addLine_fit_currentLine"
        self.lasagna.addIngredient(objectName=self.line_name, kind="lines", data=[])
        self.lasagna.returnIngredientByName(self.line_name).addToPlots()
        self.lasagna.returnIngredientByName(self.line_name).lineWidth = 4
        self.lasagna.returnIngredientByName(self.line_name).color = [0, 250, 250]

        # 3. Add a sparse point for highlighting purposes
        self.hPoint_name = "highlight_point"
        self.lasagna.addIngredient(
            objectName=self.hPoint_name, kind="sparsepoints", data=[]
        )

        self.lasagna.returnIngredientByName(self.hPoint_name).addToPlots()
        self.lasagna.returnIngredientByName(self.hPoint_name).color = [250, 0, 0]
        self.lasagna.returnIngredientByName(self.hPoint_name).symbolSize = 9
        self.lasagna.returnIngredientByName(self.hPoint_name).symbol = "s"

        self.nearest_point_index = 0  # The index of the point nearest the mouse cursor
        self.coords_of_nearest_point_to_cursor = []
        self.fit = {}  # The line fit to the sparse points
        # Otherwise plugin can crash if user selects line fit on startup with no data:
        self.fit["fit_coords"] = []
        self.lasagna.axes2D[0].listNamedItemsInPlotWidget()

        self.fitType_comboBox.addItem("No fit")
        self.fitType_comboBox.addItem("piecewise")
        self.fitType_comboBox.addItem("3D line")
        self.fitType_comboBox.addItem("2D polynomial")

        self.addPoint_radioButton.setChecked(True)

        # Set up connections
        self.deg_spinBox.valueChanged.connect(self.fit_and_display_line)
        self.clear_pushButton.clicked.connect(self.clear_line)
        self.add_pushButton.clicked.connect(self.add_line)
        self.addPoint_radioButton.toggled.connect(self.addRemoveToggle)
        self.fitType_comboBox.activated.connect(self.fit_and_display_line)
        # TODO: trigger off an edit signal not item changed
        # self.tableWidget.itemChanged.connect(self.update_from_table)

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    # The following methods are involved in shutting down the plugin window
    def closePlugin(self):
        """
        This method is called by lasagna when the user unchecks the plugin in the menu.
        """
        self.lasagna.removeIngredientByName(self.pts_name)
        self.lasagna.removeIngredientByName(self.line_name)
        self.lasagna.removeIngredientByName(self.hPoint_name)
        self.detachHooks()
        self.close()

    # We define this here because we can't assume all plugins will have QWidget::closeEvent
    def closeEvent(self, event):
        """
        This event is executed when the user presses the close window (cross) button in the title bar
        """
        self.lasagna.stopPlugin(self.__module__)  # This will call self.closePlugin
        self.lasagna.pluginActions[self.__module__].setChecked(
            False
        )  # Uncheck the menu item associated with this plugin's name
        self.deleteLater()
        event.accept()

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    # Hooks for integration with the main Lasagna GUI via methods in lasagna_object
    def hook_axisClicked(self, axis):
        """  Runs when the user clicks on an axis.

        This method handles either addition or removal of data (rows) from the the
        QTable. If adding points, the method gets the position of the mouse click
        in the stack from the main Lasagna class and adds this point to the table.
        If removing a point, it uses the identity of the point nearest the mouse
        (see self.hook_updateMainWindowOnMouseMove_End) to remove the correct row
        from the table.
        """

        pos = self.lasagna.mousePositionInStack
        if not pos:
            print("Load an image first")
            return
        elif len(pos) != 3:
            raise ValueError("I expect 3D coordinates. Got: {}".format(pos))

        hLightCoords = self.lasagna.returnIngredientByName(self.hPoint_name)._data

        # Update the add_line_plugin GUI
        if self.addPoint_radioButton.isChecked():

            # Update the text label indicating how many points have been created
            if self.lasagna.last_button_click_in_axis == 1:
                # Add point if left click
                self.num_points += 1

                # We will append the new point to the end, unless a highlight point exists,
                # in which case we append before that
                if len(hLightCoords) == 0:
                    # No highlight point
                    rowToInsert = self.num_points
                else:
                    self.nearest_point_index, _ = self.find_nearest_point_in_array(
                        self.get_points_coord(), hLightCoords
                    )
                    self.lasagna.returnIngredientByName(self.hPoint_name)._data = []
                    rowToInsert = self.nearest_point_index + 1

                self.numPoints_textLabel.setText("n pts: %d" % self.num_points)
                # self.tableWidget.setRowCount(self.num_points) #WIth add row this was problematic

                # Add clicked position to the table
                self.tableWidget.insertRow(rowToInsert - 1)
                for colIndex, textToAdd in enumerate(pos):
                    # Insert number as a string into the table at column
                    self.tableWidget.setItem(
                        rowToInsert - 1,
                        colIndex,
                        QtWidgets.QTableWidgetItem(str(textToAdd)),
                    )

            elif self.lasagna.last_button_click_in_axis == 2:
                # Mark nearest point if right-click. If the nearest point is already marked
                # then we un-mark it.
                if len(hLightCoords)>0 and np.array_equal(hLightCoords,self.coords_of_nearest_point_to_cursor):
                    self.lasagna.returnIngredientByName(self.hPoint_name)._data = []
                else:
                    self.lasagna.returnIngredientByName(
                        self.hPoint_name
                    )._data = self.coords_of_nearest_point_to_cursor


        elif self.removePoint_radioButton.isChecked():
            self.num_points -= 1
            self.tableWidget.removeRow(self.nearest_point_index)
            self.lasagna.returnIngredientByName(self.hPoint_name)._data = []

        self.lasagna.update_2D_plot_ingredients_in_axes()
        self.update_current_line()

    def hook_updateMainWindowOnMouseMove_End(self):
        """ Runs continuously when the mouse travels over an axis.

        NOTE: ** At the moment this method does nothing.  **
              ** We keep it because likely we'll need it. **
              e.g. we can run highlight point here
        """

        currentMousePos = np.array(self.lasagna.mousePositionInStack)
        existingPoints = self.get_points_coord()
        if type(existingPoints) == list or existingPoints.size == 0:
            return

        self.nearest_point_index, self.coords_of_nearest_point_to_cursor = self.find_nearest_point_in_array(
            existingPoints, currentMousePos
        )

        if self.removePoint_radioButton.isChecked():
            self.lasagna.returnIngredientByName(
                self.hPoint_name
            )._data = self.coords_of_nearest_point_to_cursor

            self.lasagna.update_2D_plot_ingredients_in_axes()

    def find_nearest_point_in_array(self, array_to_search, vector_to_find):
        """Given a 2D array, find the row that most closely matches a given vector

            Purpose
            Calculate the Euclidian distance between each row of array_to_search
            and the vector vector_to_find. Return the index of the closest match
            and its value

            :return:
            nearest_point_index - an int defining the row in array_to_search that
                            is the closest match.
            closest_match - the vector (row) in array_to_search that most closely
                            matches vector_to_find.


        """

        if len(array_to_search) == 0 or len(vector_to_find) == 0:
            return([], [])

        delta = np.sum((array_to_search - vector_to_find) ** 2, axis=1) ** 0.5

        # An integer defining the row that most closely matches the search vector
        nearestInd = delta == min(delta)
        closest_match = array_to_search[nearestInd, :]
        nearest_point_index = np.where(nearestInd)[0].tolist()[0]

        return (nearest_point_index, closest_match)

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    # Remaining methods which are not hooks or otherwise obligatory
    def add_line(self):
        """Add the current line and points to Lasagna and start a new line

           i.e. This is not the method that updates. It commits the existing
           data to Lasagna and allows for more to be created.

        :return:
        """

        pts_name = "%s_pts" % self.name_lineEdit.text()
        self.lasagna.addIngredient(
            objectName=pts_name, kind="sparsepoints", data=self.get_points_coord()
        )
        self.lasagna.returnIngredientByName(pts_name).addToPlots()  # Add i

        if self.fit:
            data = self.fit["fit_coords"]
            line_name = "%s_fit" % self.name_lineEdit.text()
            self.lasagna.addIngredient(objectName=line_name, kind="lines", data=data)
            # Add item to all three 2D plots
            self.lasagna.returnIngredientByName(line_name).addToPlots()

        self.clear_line()

    def addRemoveToggle(self):
        """
        This slot runs when the user interacts with the add/remove radio buttons
        """
        if self.addPoint_radioButton.isChecked():
            # Remove the highlight point
            self.lasagna.returnIngredientByName(self.hPoint_name)._data = []
            self.lasagna.update_2D_plot_ingredients_in_axes()
        elif self.removePoint_radioButton.isChecked():
            pass

    def clear_line(self):
        """Clear current line

        :return:
        """
        self.num_points = 0
        self.tableWidget.clear()
        self.tableWidget.setRowCount(0)
        self.numPoints_textLabel.setText("n pts: %d" % 0)
        self.lasagna.returnIngredientByName(self.hPoint_name)._data = []
        self.lasagna.returnIngredientByName(self.line_name)._data = []
        self.lasagna.update_2D_plot_ingredients_in_axes()
        self.fit_and_display_line()
        self.update_current_line()

    def update_current_line(self):
        """Change current line ingredient and display points

        :return:
        """
        coords = self.get_points_coord()  # Existing coordinates
        pts = self.lasagna.returnIngredientByName(self.pts_name)  # What is plotted

        changed = False
        if len(coords) != len(pts.raw_data()):
            changed = True
        elif len(coords) and any(coords.flatten() != pts.raw_data().flatten()):
            changed = True
        if not changed:
            return

        pts._data = coords
        self.lasagna.update_2D_plot_ingredients_in_axes()
        self.fit_and_display_line(coords)

        return

    def fit_and_display_line(self, *args, **kwargs):
        """Polynomial fit of the points.

        Try to fit y = f(x) and x = f(y) and keep the version with lowest residuals to take
        care of vertical lines

        :return:
        """
        # The *args catches whatever the connected slot might send (deg for deg_spinBox.valueChanged
        #         for instance)

        # The columns in coords are [optical plane, coronal plane X, coronal plane Y]
        if "coords" in kwargs:
            coords = kwargs["coords"]
        else:
            coords = self.get_points_coord()

        if self.fitType_comboBox.currentText() == "2D polynomial":
            self.fit_this_line_coronal(coords)
        elif self.fitType_comboBox.currentText() == "3D line":
            self.fit_this_line_svd(coords)
        elif self.fitType_comboBox.currentText() == "piecewise":
            self.link_points_with_line(coords)
        elif self.fitType_comboBox.currentText() == "No fit":
            self.fit["fit_coords"] = []
        else:
            print("Unknown fit type '%s'" % self.fitType_comboBox.currentText())
            return

        line = self.lasagna.returnIngredientByName(self.line_name)
        line._data = self.fit["fit_coords"]

        self.lasagna.update_2D_plot_ingredients_in_axes()

    def fit_this_line_coronal(self, coords):
        """ Fits a 2D line with a polynomial
            This method is called by fit_and_display_line
            The columns in coords are [optical plane, coronal plane X, coronal plane Y]

            : return:
            None - all fit information will bein self.fit
        """

        deg = self.deg_spinBox.value()
        if len(coords) <= deg:
            print("Need at least %i points to fit" % (deg + 1))
            self.fit = {}
            self.fit["fit_coords"] = []
            return

        deg = self.deg_spinBox.value()  # Polynomial fit order

        coefs_x = np.polyfit(coords[:, 1], coords[:, 2], deg)
        fit_x = np.poly1d(coefs_x)
        res_x = np.sum((coords[:, 2] - fit_x(coords[:, 1])) ** 2)

        coefs_y = np.polyfit(coords[:, 2], coords[:, 1], deg)
        fit_y = np.poly1d(coefs_y)
        res_y = np.sum((coords[:, 1] - fit_y(coords[:, 2])) ** 2)

        if res_x <= res_y:
            self.fit = dict(is_x_y=True, fit=fit_x, coefs=coefs_x)
        else:
            self.fit = dict(is_x_y=False, fit=fit_y, coefs=coefs_y)

        if self.fit["is_x_y"]:
            fit_data = np.arange(coords[:, 1].min(), coords[:, 1].max())
            replaced_ax = 2
        else:
            fit_data = np.arange(coords[:, 2].min(), coords[:, 2].max())
            replaced_ax = 1

        line_coords = np.repeat(fit_data, 3).reshape((-1, 3))
        line_coords[:, 0] = self.lasagna.axes2D[0].currentSlice
        line_coords[:, replaced_ax] = self.fit["fit"](fit_data)
        self.fit["fit_coords"] = line_coords

    def fit_this_line_svd(self, coords):
        """ Fits a 3D line with SVD
            This method is called by fit_and_display_line
            The columns in coords are [optical plane, coronal plane X, coronal plane Y]

            : return:
            None - all fit information will bein self.fit
        """
        if len(coords) < 2:
            return

        muCoords = coords.mean(axis=0)

        # Do an SVD on the mean-centered data.
        tData = coords - muCoords
        uu, dd, vv = np.linalg.svd(tData)
        datMin = tData.flatten().min()
        datMax = tData.flatten().max()
        linepts = vv[0] * np.mgrid[datMin:datMax:2][:, np.newaxis]
        linepts += muCoords

        linepts = np.unique(np.round(linepts), axis=0)
        self.fit["fit_coords"] = linepts

    def link_points_with_line(self, coords):
        if len(coords) < 2:
            return

        # Linearly interpolate between the points
        for ii in range(coords.shape[0]-1):
            delta = sum((coords[ii, :] - coords[ii+1,:])**2)**0.5
            nSteps = int(round(delta) + 2)

            tmp = np.linspace(coords[ii, :], coords[ii+1,:], num=nSteps)
            if ii == 0:
                cInt = tmp
            else:
                cInt = np.concatenate((cInt, tmp))

        cInt = np.around(cInt, decimals=3)

        self.fit["fit_coords"] = cInt

    def get_points_coord(self):
        """Return the coordinates of points in the table

        :return:
        """
        if self.tableWidget.rowCount() == 0:
            return []

        output_points = []
        for i in range(self.tableWidget.rowCount()):
            coords = [int(self.tableWidget.item(i, c).text()) for c in range(0, 3)]
            output_points.append(np.array(coords, dtype=float))
        return np.vstack(output_points)

    def update_from_table(self):
        """If the table cells all contain data then we can update the plot based upon this

        :return:
        """
        # TODO: ensure all text boxes have something in them and then update the plots
