"""Shared, axis-specific auto margins and display styling."""
import pyqtgraph as pg


class PaddedViewBox(pg.ViewBox):
    def __init__(self, **kwargs):
        self.axis_padding = (0.02, 0.05)
        super().__init__(**kwargs)

    def suggestPadding(self, axis):
        return self.axis_padding[axis]


def color_with_alpha(color, alpha):
    result = pg.mkColor(color)
    result.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return result


def apply_plot_options(plot, settings, prefix):
    view = plot.getViewBox()
    if isinstance(view, PaddedViewBox):
        view.axis_padding = (getattr(settings, prefix + '_x_margin'),
                             getattr(settings, prefix + '_y_margin'))
        view.updateAutoRange()
    plot.showGrid(x=settings.show_grid, y=settings.show_grid, alpha=settings.grid_alpha)
    legend = plot.getPlotItem().legend
    if legend is not None:
        legend.setVisible(settings.show_legend)
