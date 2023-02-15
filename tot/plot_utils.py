import logging

import numpy as np
import pandas as pd
import plotly.graph_objs as go
from plotly_resampler import register_plotly_resampler, unregister_plotly_resampler

from tot.df_utils import prep_or_copy_df

log = logging.getLogger("tot.plot")


# UI Configuration
prediction_color = "#2d92ff"
actual_color = "black"
trend_color = "#B23B00"
line_width = 2
marker_size = 4
xaxis_args = {
    "showline": True,
    "mirror": True,
    "linewidth": 1.5,
}
yaxis_args = {
    "showline": True,
    "mirror": True,
    "linewidth": 1.5,
}
layout_args = {
    "autosize": True,
    "template": "plotly_white",
    "margin": go.layout.Margin(l=0, r=10, b=0, t=10, pad=0),
    "font": dict(size=10),
    "title": dict(font=dict(size=12)),
    "hovermode": "x unified",
}


def log_warning_colab_resampler():
    log.warning(
        "Warning: plotly-resampler not supported for google colab environment. "
        "Plotting backend automatically switched to 'plotly' without resampling "
    )


def log_warning_static_env_resampler():
    log.warning(
        "Warning: plotly-resampler not supported for this environments. "
        "Plotting backend automatically switched to 'plotly' without resampling "
    )


def log_value_error_invalid_plotting_backend_input():
    raise ValueError(
        "Selected plotting backend invalid. Set plotting backend to one of the "
        "valid options 'plotly','plotly-auto','plotly-resampler'."
    )


def log_value_error_invalid_highlight_forecast_input():
    raise ValueError(
        "input for highlight_forecast invalid. Set highlight_forecast step equal to"
        " or smaller than the prediction horizon"
    )


def validate_current_env():
    """
    Validate the current environment to check if it is a valid environment to run the code.

    Returns
    -------
    bool :
        True if the current environment is a valid environment to run the code, False otherwise.

    """
    from IPython.core.getipython import get_ipython

    if "google.colab" in str(get_ipython()):
        log_warning_colab_resampler()
        vaild_env = False
    else:
        if is_notebook():
            vaild_env = True
        else:
            log_warning_static_env_resampler()
            vaild_env = False
    return vaild_env


def is_notebook():
    """
    Determine if the code is being executed in a Jupyter notebook environment.

    Returns
    -------
    bool :
        True if the code is being executed in a Jupyter notebook, False otherwise.
    """
    try:
        from IPython.core.getipython import get_ipython

        if "IPKernelApp" not in str(get_ipython()):  # pragma: no cover
            return False
    except ImportError:
        return False
    except AttributeError:
        return False
    return True


def auto_set_plotting_backend(plotting_backend_original):
    """
    Automatically set the plotting backend.

    Given `plotting_backend_original`, returns "plotly-resample" if `validate_current_env()`
    returns `True` and `plotting_backend_original` is "plotly-auto", "plotly" otherwise. If
    `plotting_backend_original` is not "plotly-auto", returns `plotting_backend_original`.

    Parameters
    ----------
    plotting_backend_original : str
        Original plotting backend.

    Returns
    -------
    str
        The new plotting backend.
    """
    if plotting_backend_original == "plotly-auto":
        plotting_backend_new = "plotly-resample" if validate_current_env() else "plotly"
    else:
        plotting_backend_new = plotting_backend_original
    return plotting_backend_new


def validate_plotting_backend_input(plotting_backend):
    """
    Validate the input argument for the plotting backend.

    Parameters
    ----------
    plotting_backend:str
        The name of the plotting backend.

    Raises
    ----------
    ValueError:
        If the plotting backend is not a valid backend.

    Returns
    ----------
        None
    """
    valid_plotting_backends = ["plotly", "plotly-auto", "plotly-resampler"]
    if plotting_backend in valid_plotting_backends:
        pass
    else:
        log_value_error_invalid_plotting_backend_input()


def validate_highlight_forecast_input(highlight_forecast, fcst):
    """
    Validate the input argument for the highlight_forecast.

    Parameters
    ----------
    highlight_forecast : int
        The number of forecasts to highlight.
    fcst : pd.DataFrame
        The forecast DataFrame.

    Raises
    ------
    ValueError
        If the highlight_forecast value is greater than the number of yhat (prediction horizon) columns in fcst.

    Returns
    -------
    None
    """
    n_yhat = len([col for col in fcst.columns if "yhat" in col])
    if highlight_forecast is None:
        pass
    elif highlight_forecast <= n_yhat:
        pass
    else:
        log_value_error_invalid_highlight_forecast_input()


def validate_df_name_input(df_name, fcst):
    """
    Validate the input df_name and returns a dataframe with a single time series and an ID.

    Parameters
    ----------
    df_name : str
        optoinal, ID from time series that should be plotted
    fcst : pd.DataFrame
        forecast dataframe

    Returns
    -------
    pd.DataFrame
        A copy of the input dataframe containing the time series data for the specified name.

    Raises
    ------
    Exception
        If the input DataFrame contains more than one time series and the df_name argument is not provided, or if the
        specified df_name is not present in the DataFrame.

    """
    fcst, received_ID_col, received_single_time_series, _ = prep_or_copy_df(fcst)
    if not received_single_time_series:
        if df_name not in fcst["ID"].unique():
            assert len(fcst["ID"].unique()) > 1
            raise Exception(
                "Many time series are present in the pd.DataFrame (more than one ID). Please, especify ID to be plotted."
            )
        else:
            fcst = fcst[fcst["ID"] == df_name].copy(deep=True)
            log.info(f"Plotting data from ID {df_name}")
    return fcst


def _plot_plotly(
    fcst,
    quantiles=[0.5],
    xlabel="ds",
    ylabel="y",
    highlight_forecast=None,
    figsize=(700, 350),
    resampler_active=False,
):
    """
    Plot the NeuralProphet forecast

    Parameters
    ---------
        fcst : pd.DataFrame
            Output of m.predict
        quantiles: list
            Quantiles for which the forecasts are to be plotted.
        xlabel : str
            Label name on X-axis
        ylabel : str
            Label name on Y-axis
        highlight_forecast : Union[int, None]
            i-th step ahead forecast to highlight.
        line_per_origin : bool
            Print a line per forecast of one per forecast age
        figsize : tuple [int, int]
            Width, height in inches.

    Returns
    -------
        Plotly figure
    """
    if resampler_active:
        register_plotly_resampler(mode="auto")
    else:
        unregister_plotly_resampler()
    cross_marker_color = "blue"
    cross_symbol = "x"

    fcst = fcst.fillna(value=np.nan)

    ds = fcst["ds"].dt.to_pydatetime()
    colname = "yhat"
    step = 1
    # all yhat column names, including quantiles
    yhat_col_names = [col_name for col_name in fcst.columns if f"{colname}" in col_name]
    # without quants
    yhat_col_names_no_qts = [
        col_name for col_name in yhat_col_names if f"{colname}" in col_name and "%" not in col_name
    ]
    data = []

    if highlight_forecast is None:
        for i, yhat_col_name in enumerate(yhat_col_names_no_qts):
            data.append(
                go.Scatter(
                    name=yhat_col_name,
                    x=ds,
                    y=fcst[f"{colname}{i + 1}"],
                    mode="lines",
                    line=dict(color=f"rgba(45, 146, 255, {0.2 + 2.0 / (i + 2.5)})", width=line_width),
                    fill="none",
                )
            )
    if len(quantiles) > 1:
        for i in range(1, len(quantiles)):
            # skip fill="tonexty" for the first quantile
            if i == 1:
                data.append(
                    go.Scatter(
                        name=f"{colname}{highlight_forecast if highlight_forecast else step} {round(quantiles[i] * 100, 1)}%",
                        x=ds,
                        y=fcst[
                            f"{colname}{highlight_forecast if highlight_forecast else step} {round(quantiles[i] * 100, 1)}%"
                        ],
                        mode="lines",
                        line=dict(color="rgba(45, 146, 255, 0.2)", width=1),
                        fillcolor="rgba(45, 146, 255, 0.2)",
                    )
                )
            else:
                data.append(
                    go.Scatter(
                        name=f"{colname}{highlight_forecast if highlight_forecast else step} {round(quantiles[i] * 100, 1)}%",
                        x=ds,
                        y=fcst[
                            f"{colname}{highlight_forecast if highlight_forecast else step} {round(quantiles[i] * 100, 1)}%"
                        ],
                        mode="lines",
                        line=dict(color="rgba(45, 146, 255, 0.2)", width=1),
                        fill="tonexty",
                        fillcolor="rgba(45, 146, 255, 0.2)",
                    )
                )

    if highlight_forecast is not None:
        x = ds
        y = fcst[f"yhat{highlight_forecast}"]
        data.append(
            go.Scatter(
                name="Predicted",
                x=x,
                y=y,
                mode="lines",
                line=dict(color=prediction_color, width=line_width),
            )
        )
        data.append(
            go.Scatter(
                name="Predicted",
                x=x,
                y=y,
                mode="markers",
                marker=dict(color=cross_marker_color, size=marker_size, symbol=cross_symbol),
            )
        )

    # Add actual
    data.append(
        go.Scatter(name="Actual", x=ds, y=fcst["y"], marker=dict(color=actual_color, size=marker_size), mode="markers")
    )

    layout = go.Layout(
        showlegend=True,
        width=figsize[0],
        height=figsize[1],
        xaxis=go.layout.XAxis(
            title=xlabel,
            type="date",
            rangeselector=dict(
                buttons=list(
                    [
                        dict(count=7, label="1w", step="day", stepmode="backward"),
                        dict(count=1, label="1m", step="month", stepmode="backward"),
                        dict(count=6, label="6m", step="month", stepmode="backward"),
                        dict(count=1, label="1y", step="year", stepmode="backward"),
                        dict(step="all"),
                    ]
                )
            ),
            rangeslider=dict(visible=True),
            **xaxis_args,
        ),
        yaxis=go.layout.YAxis(title=ylabel, **yaxis_args),
        **layout_args,
    )
    fig = go.Figure(data=data, layout=layout)
    return fig
