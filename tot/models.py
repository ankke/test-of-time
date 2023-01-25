import logging
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import Type

import numpy as np
import pandas as pd
from neuralprophet import NeuralProphet, TorchProphet, df_utils

from tot.df_utils import reshape_raw_predictions_to_forecast_df
from tot.utils import _convert_seasonality_to_season_length, _get_seasons, convert_df_to_TimeSeries

# check import of implemented models and consider order of imports
try:
    from sklearn.linear_model import LinearRegression

    _sklearn_installed = True
except ImportError:
    LinearRegression = None
    _sklearn_installed = False
    raise ImportError(
        "The LinearRegression model could not be imported."
        "Check for proper installation of sklearn: https://scikit-learn.org/stable/install.html"
    )

try:
    from darts.models import RegressionModel

    _darts_installed = True
except ImportError:
    RegressionModel = None
    _darts_installed = False
    raise ImportError(
        "The RegressionModel could not be imported."
        "Check for proper installation of darts: https://github.com/unit8co/darts/blob/master/INSTALL.md"
    )

try:
    from prophet import Prophet

    _prophet_installed = True
except ImportError:
    Prophet = None
    _prophet_installed = False

    raise ImportError(
        "The Prophet model could not be imported."
        "Check for proper installation of prophet: https://facebook.github.io/prophet/docs/installation.html"
    )


log = logging.getLogger("tot.model")


@dataclass
class Model(ABC):
    """
    example use:
    >>> models = []
    >>> for params in [{"n_changepoints": 5}, {"n_changepoints": 50},]:
    >>>     models.append(Model(
    >>>         params=params
    >>>         model_name="NeuralProphet",
    >>>         model_class=NeuralProphet,
    >>>     ))
    """

    params: dict
    model_name: str

    @abstractmethod
    def fit(self, df: pd.DataFrame, freq: str):
        pass

    @abstractmethod
    def predict(self, df: pd.DataFrame, df_historic: pd.DataFrame = None):
        pass

    def _handle_missing_data(self, df, freq, predicting=False):
        """
        if Model does not provide own data handling method: handles missing data
        else (time-features only): returns unchanged df
        """
        return df

    def maybe_add_first_inputs_to_df(self, df_train, df_test):
        """Adds last n_lags values from df_train to start of df_test."""
        if self.n_lags > 0:
            df_train, _, _, _ = df_utils.prep_or_copy_df(df_train)
            (
                df_test,
                received_ID_col_test,
                received_single_time_series_test,
                _,
            ) = df_utils.prep_or_copy_df(df_test)
            df_test_new = pd.DataFrame()
            for df_name, df_test_i in df_test.groupby("ID"):
                df_train_i = df_train[df_train["ID"] == df_name].copy(deep=True)
                df_test_i = pd.concat(
                    [df_train_i.tail(self.n_lags), df_test_i],
                    ignore_index=True,
                )
                df_test_new = pd.concat((df_test_new, df_test_i), ignore_index=True)
            df_test = df_utils.return_df_in_original_format(
                df_test_new,
                received_ID_col_test,
                received_single_time_series_test,
            )
        return df_test

    def maybe_drop_first_forecasts(self, predicted, df):
        """
        if Model with lags: removes first n_lags values from predicted and df
        else (time-features only): returns unchanged df
        """
        if self.n_lags > 0:
            (
                predicted,
                received_ID_col_pred,
                received_single_time_series_pred,
                _,
            ) = df_utils.prep_or_copy_df(predicted)
            (
                df,
                received_ID_col_df,
                received_single_time_series_df,
                _,
            ) = df_utils.prep_or_copy_df(df)
            predicted_new = pd.DataFrame()
            df_new = pd.DataFrame()
            for df_name, df_i in df.groupby("ID"):
                predicted_i = predicted[predicted["ID"] == df_name].copy(deep=True)
                predicted_i = predicted_i[self.n_lags :]
                df_i = df_i[self.n_lags :]
                df_new = pd.concat((df_new, df_i), ignore_index=True)
                predicted_new = pd.concat((predicted_new, predicted_i), ignore_index=True)
            df = df_utils.return_df_in_original_format(df_new, received_ID_col_df, received_single_time_series_df)
            predicted = df_utils.return_df_in_original_format(
                predicted_new,
                received_ID_col_pred,
                received_single_time_series_pred,
            )
        return predicted, df

    def maybe_drop_added_dates(self, predicted, df):
        """if Model imputed any dates: removes any dates in predicted which are not in df_test."""
        return predicted.reset_index(drop=True), df.reset_index(drop=True)


@dataclass
class ProphetModel(Model):
    model_name: str = "Prophet"
    model_class: Type = Prophet

    def __post_init__(self):
        if not _prophet_installed:
            raise RuntimeError("Requires prophet to be installed")
        data_params = self.params["_data_params"]
        custom_seasonalities = None
        if "seasonalities" in data_params and len(data_params["seasonalities"]) > 0:
            daily, weekly, yearly, custom_seasonalities = _get_seasons(data_params["seasonalities"])
            self.params.update({"daily_seasonality": daily})
            self.params.update({"weekly_seasonality": weekly})
            self.params.update({"yearly_seasonality": yearly})
        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        self.model = self.model_class(**model_params)
        if custom_seasonalities is not None:
            for seasonality in custom_seasonalities:
                self.model.add_seasonality(
                    name="{}_daily".format(str(seasonality)),
                    period=seasonality,
                )
        self.n_forecasts = 1
        self.n_lags = 0
        self.season_length = None

    def fit(self, df: pd.DataFrame, freq: str):
        if "ID" in df.columns and len(df["ID"].unique()) > 1:
            raise NotImplementedError("Prophet does not work with many ts df")
        self.freq = freq
        self.model = self.model.fit(df=df)

    def predict(self, df: pd.DataFrame, df_historic: pd.DataFrame = None):
        fcst = self.model.predict(df=df)
        fcst_df = pd.DataFrame({"time": fcst.ds, "y": df.y, "yhat1": fcst.yhat})
        return fcst_df

    def maybe_add_first_inputs_to_df(self, df_train, df_test):
        """
        if historic data is used as input to the model to make prediction: adds number of past observations
        (e.g. n_lags or season_length) values to start of df_test.
        else (time-features only): returns unchanged df_test.
        """
        return df_test.reset_index(drop=True)

    def maybe_drop_first_forecasts(self, predicted, df):
        """
        if historic data is used as input to the model to make prediction: removes number of past observations
        (e.g. n_lags or season_length) values from predicted and df_test.
        else (time-features only): returns unchanged df_test.
        """
        return predicted.reset_index(drop=True), df.reset_index(drop=True)


@dataclass
class NeuralProphetModel(Model):
    model_name: str = "NeuralProphet"
    model_class: Type = NeuralProphet

    def __post_init__(self):
        data_params = self.params["_data_params"]
        custom_seasonalities = None
        if "seasonalities" in data_params and len(data_params["seasonalities"]) > 0:
            daily, weekly, yearly, custom_seasonalities = _get_seasons(data_params["seasonalities"])
            self.params.update({"daily_seasonality": daily})
            self.params.update({"weekly_seasonality": weekly})
            self.params.update({"yearly_seasonality": yearly})
        if "seasonality_mode" in data_params and data_params["seasonality_mode"] is not None:
            self.params.update({"seasonality_mode": data_params["seasonality_mode"]})
        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        self.model = self.model_class(**model_params)
        if custom_seasonalities is not None:
            for seasonality in custom_seasonalities:
                self.model.add_seasonality(
                    name="{}_daily".format(str(seasonality)),
                    period=seasonality,
                )
        self.n_forecasts = self.model.n_forecasts
        self.n_lags = self.model.n_lags
        self.season_length = None

    def fit(self, df: pd.DataFrame, freq: str):
        self.freq = freq
        _ = self.model.fit(df=df, freq=freq, progress="none", minimal=True)

    def predict(self, df: pd.DataFrame, df_historic: pd.DataFrame = None):
        if df_historic is not None:
            df = self.maybe_add_first_inputs_to_df(df_historic, df)
        fcst = self.model.predict(df=df)
        (
            fcst,
            received_ID_col,
            received_single_time_series,
            _,
        ) = df_utils.prep_or_copy_df(fcst)
        fcst_df = pd.DataFrame()
        for df_name, fcst_i in fcst.groupby("ID"):
            y_cols = ["y"] + [col for col in fcst_i.columns if "yhat" in col]
            fcst_aux = pd.DataFrame({"time": fcst_i.ds})
            for y_col in y_cols:
                fcst_aux[y_col] = fcst_i[y_col]
            fcst_aux["ID"] = df_name
            fcst_df = pd.concat((fcst_df, fcst_aux), ignore_index=True)
        fcst_df = df_utils.return_df_in_original_format(fcst_df, received_ID_col, received_single_time_series)
        if df_historic is not None:
            fcst_df, df = self.maybe_drop_first_forecasts(fcst_df, df)
        fcst_df, df = self.maybe_drop_added_dates(fcst_df, df)
        return fcst_df


@dataclass
class TorchProphetModel(NeuralProphetModel):
    model_name: str = "TorchProphet"
    model_class: Type = TorchProphet

    def __post_init__(self):
        data_params = self.params["_data_params"]
        custom_seasonalities = None
        if "seasonalities" in data_params and len(data_params["seasonalities"]) > 0:
            daily, weekly, yearly, custom_seasonalities = _get_seasons(data_params["seasonalities"])
            self.params.update({"daily_seasonality": daily})
            self.params.update({"weekly_seasonality": weekly})
            self.params.update({"yearly_seasonality": yearly})
        if "seasonality_mode" in data_params and data_params["seasonality_mode"] is not None:
            self.params.update({"seasonality_mode": data_params["seasonality_mode"]})
        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        model_params.update({"interval_width": 0})
        self.model = self.model_class(**model_params)
        if custom_seasonalities is not None:
            for seasonality in custom_seasonalities:
                self.model.add_seasonality(
                    name="{}_daily".format(str(seasonality)),
                    period=seasonality,
                )
        self.n_forecasts = self.model.n_forecasts
        self.n_lags = self.model.n_lags
        self.season_length = None

    def maybe_add_first_inputs_to_df(self, df_train, df_test):
        """
        if historic data is used as input to the model to make prediction: adds number of past observations
        (e.g. n_lags or season_length) values to start of df_test.
        else (time-features only): returns unchanged df_test.
        """
        return df_test.reset_index(drop=True)

    def maybe_drop_first_forecasts(self, predicted, df):
        """
        if historic data is used as input to the model to make prediction: removes number of past observations
        (e.g. n_lags or season_length) values from predicted and df_test.
        else (time-features only): returns unchanged df_test.
        """
        return predicted.reset_index(drop=True), df.reset_index(drop=True)


@dataclass
class SeasonalNaiveModel(Model):
    """
    A `SeasonalNaiveModel` is a naive model that forecasts future values of a target series based on past observations
    of the target series of the specified period, i.e season.

    Parameters
    ----------
        season_length : int
            seasonal period in number of time steps
        n_forecasts : int
            number of steps ahead of prediction time step to forecast
    Note
    ----
        ``Supported capabilities``
        * univariate time series
        * n_forecats > 1

        ``Not supported capabilities``
        * multivariate time series input

    """

    model_name: str = "SeasonalNaive"

    def __post_init__(self):
        # no installation checks required

        # re-assign _data_params
        data_params = self.params["_data_params"]
        self.freq = data_params["freq"]
        custom_seasonalities = None
        if "seasonalities" in data_params and len(data_params["seasonalities"]) > 0:
            daily, weekly, yearly, custom_seasonalities = _get_seasons(data_params["seasonalities"])
            self.params.update({"daily_seasonality": daily})
            self.params.update({"weekly_seasonality": weekly})
            self.params.update({"yearly_seasonality": yearly})
        if "seasonality_mode" in data_params and data_params["seasonality_mode"] is not None:
            self.params.update({"seasonality_mode": data_params["seasonality_mode"]})

        # Verify expected model_params: season_length > 1, n_forecasts >=1
        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        self.n_forecasts = model_params["n_forecasts"]
        assert self.n_forecasts >= 1, "Model parameter n_forecasts must be >=1. "

        self.season_length = None
        # always select seasonality provided by dataset first
        if "seasonalities" in data_params and len(data_params["seasonalities"]) > 0:
            self.season_length = _convert_seasonality_to_season_length(
                data_params["freq"],
                daily,
                weekly,
                yearly,
                custom_seasonalities,
            )
        elif "season_length" in model_params:
            self.season_length = model_params["season_length"]  # for seasonal naive season_length is input parameter
        assert self.season_length is not None, (
            "Dataset does not provide a seasonality. Assign a seasonality to each of the datasets "
            "OR input desired season_length as model parameter to be used for all datasets "
            "without specified seasonality."
        )
        assert (
            self.season_length > 1
        ), "season_length must be >1 for SeasonalNaiveModel. For season_length=1 select NaiveModel instead."
        self.n_lags = None  # TODO: should not be set to None. Find different solution.

    def fit(self, df: pd.DataFrame, freq: str):
        pass

    def predict(self, df: pd.DataFrame, df_historic: pd.DataFrame = None):
        """Runs the model to make predictions.
        Expects all data to be present in dataframe.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally ``ID`` with data
            df_historic : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally ``ID`` with historic data
        Returns
        -------
            pd.DataFrame
                columns ``ds``, ``y``, optionally [``ID``], and [``yhat<i>``] where yhat<i> refers to the
                i-step-ahead prediction for this row's datetime, e.g. yhat3 is the prediction for this datetime,
                predicted 3 steps ago, "3 steps old".

                Note
                ----
                 *  raw data is not supported
        """
        if df_historic is not None:
            df = self.maybe_add_first_inputs_to_df(df_historic, df)
        (
            df,
            received_ID_col,
            received_single_time_series,
            _,
        ) = df_utils.prep_or_copy_df(df)
        # Receives df with single ID column. Only single time series accepted.
        assert len(df["ID"].unique()) == 1  # TODO: add multi-ID, multi-target

        forecast = pd.DataFrame
        # check also no id column
        for df_name, df_i in df.groupby("ID"):
            dates, predicted = self._predict_raw(df_i)
            forecast = reshape_raw_predictions_to_forecast_df(
                df_i,
                predicted,
                n_req_past_observations=self.season_length,
                n_req_future_observations=self.n_forecasts,
            )
        fcst_df = df_utils.return_df_in_original_format(forecast, received_ID_col, received_single_time_series)
        if df_historic is not None:
            fcst_df, df = self.maybe_drop_first_forecasts(fcst_df, df)
        fcst_df, df = self.maybe_drop_added_dates(fcst_df, df)
        return fcst_df

    def maybe_add_first_inputs_to_df(self, df_train, df_test):
        """Adds last season_length values from df_train to start of df_test.

        Parameters
        ----------
            df_train: pd.DataFrame
                dataframe containing train data
            df_test: pd.DataFrame
                dataframe containing test data of previous split

        Returns
        -------
            pd.DataFrame
                dataframe containing test data enlarged with season_length values.
        """
        df_train, _, _, _ = df_utils.prep_or_copy_df(df_train.tail(self.season_length))
        (
            df_test,
            received_ID_col_test,
            received_single_time_series_test,
            _,
        ) = df_utils.prep_or_copy_df(df_test)
        df_test_new = pd.DataFrame()
        for df_name, df_test_i in df_test.groupby("ID"):
            df_train_i = df_train[df_train["ID"] == df_name].copy(deep=True)
            df_test_i = pd.concat(
                [df_train_i.tail(self.season_length), df_test_i],
                ignore_index=True,
            )
            df_test_new = pd.concat((df_test_new, df_test_i), ignore_index=True)
        df_test = df_utils.return_df_in_original_format(
            df_test_new, received_ID_col_test, received_single_time_series_test
        )
        return df_test

    def maybe_drop_first_forecasts(self, predicted, df):
        """
        Removes first season_length values from predicted and df that have been previously added.

        Parameters
        ----------
            predicted: pd.DataFrame
                dataframe containing predicted data
            df: pd.DataFrame
                dataframe containing initial data

        Returns
        -------
            pd.DataFrame
                dataframe containing predicted data reduced by the first season_length values.
            pd.DataFrame
                dataframe containing initial data reduced by the first season_length values.
        """
        if self.season_length > 0:
            (
                predicted,
                received_ID_col_pred,
                received_single_time_series_pred,
                _,
            ) = df_utils.prep_or_copy_df(predicted)
            (
                df,
                received_ID_col_df,
                received_single_time_series_df,
                _,
            ) = df_utils.prep_or_copy_df(df)
            predicted_new = pd.DataFrame()
            df_new = pd.DataFrame()
            for df_name, df_i in df.groupby("ID"):
                predicted_i = predicted[predicted["ID"] == df_name].copy(deep=True)
                predicted_i = predicted_i[self.season_length :]
                df_i = df_i[self.season_length :]
                df_new = pd.concat((df_new, df_i), ignore_index=True)
                predicted_new = pd.concat((predicted_new, predicted_i), ignore_index=True)
            df = df_utils.return_df_in_original_format(df_new, received_ID_col_df, received_single_time_series_df)
            predicted = df_utils.return_df_in_original_format(
                predicted_new,
                received_ID_col_pred,
                received_single_time_series_pred,
            )
        return predicted, df

    def _predict_raw(self, df):
        """Computes forecast-origin-wise seasonal naive predictions.
        Predictions are returned in vector format. Predictions are given on a forecast origin basis,
        not on a target basis.
        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally``ID`` with all data
        Returns
        -------
            pd.Series
                timestamps referring to the start of the predictions.
            np.array
                array containing the predictions
        """
        # Receives df with single ID column
        assert len(df["ID"].unique()) == 1

        dates = df["ds"].iloc[self.season_length : -self.n_forecasts + 1].reset_index(drop=True)
        # assemble last values based on season_length
        last_k_vals_arrays = [df["y"].iloc[i : i + self.season_length].values for i in range(0, dates.shape[0])]
        last_k_vals = np.stack(last_k_vals_arrays, axis=0)
        # Compute the predictions
        predicted = np.array([last_k_vals[:, i % self.season_length] for i in range(self.n_forecasts)]).T

        # No un-scaling and un-normalization needed. Operations not applicable for naive model
        return dates, predicted


@dataclass()
class NaiveModel(SeasonalNaiveModel):
    """
    A `NaiveModel` is a naive model that forecasts future values of a target series as the value of the
    last observation of the target series. The NaiveModel is SeasonalNaiveModel with K=1.

    Parameters
    ----------
        n_forecasts : int
            number of steps ahead of prediction time step to forecast
    """

    model_name: str = "NaiveModel"

    def __post_init__(self):
        # no installation checks required

        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        self.n_forecasts = model_params["n_forecasts"]
        assert self.n_forecasts >= 1, "Model parameter n_forecasts must be >=1. "
        self.n_lags = None  # TODO: should not be set to None. Find different solution.
        self.season_length = 1  # season_length=1 for NaiveModel


@dataclass
class LinearRegressionModel(Model):
    """
     A forecasting model using a linear regression of  the target series' lags to obtain a forecast.

     Parameters
     ----------
         n_lags : int
             Previous time series steps to include in auto-regression. Aka AR-order
         output_chunk_length : int
             Number of time steps predicted at once by the internal regression model. Does not have to equal the forecast
             horizon `n` used in `predict()`. However, setting `output_chunk_length` equal to the forecast horizon may
             be useful if the covariates don't extend far enough into the future.
         model : Type
             Scikit-learn-like model with ``fit()`` and ``predict()`` methods. Also possible to use model that doesn't
             support multi-output regression for multivariate timeseries, in which case one regressor
             will be used per component in the multivariate series.
             If None, defaults to: ``sklearn.linear_model.LinearRegression(n_jobs=-1)``.
         multi_models : bool
             If True, a separate model will be trained for each future lag to predict. If False, a single model is
             trained to predict at step 'output_chunk_length' in the future. Default: True.

     Examples
     --------
     >>> model_classes_and_params = [
     >>>     (
     >>>         LinearRegressionModel,
     >>>         {"lags": 12, "output_chunk_length": 4, "n_forecasts": 4},
     >>>     ),
     >>> ]
     >>>
     >>> benchmark = SimpleBenchmark(
     >>>     model_classes_and_params=model_classes_and_params,
     >>>     datasets=dataset_list,
     >>>     metrics=list(ERROR_FUNCTIONS.keys()),
     >>>     test_percentage=25,
     >>>     save_dir=SAVE_DIR,
     >>>     num_processes=1,
     >>> )

         Note
    ----
        ``Supported capabilities``
        * univariate time series
        * n_forecats > 1
        * autoregression


        ``Not supported capabilities``
        * multivariate time series input
        * frequency check and optional frequency conversion
    """

    model_name: str = "LinearRegressionModel"
    model_class: Type = RegressionModel

    def __post_init__(self):
        # check if installed
        if not (_darts_installed or _sklearn_installed):
            raise RuntimeError(
                "Requires darts and sklearn to be installed:"
                "https://scikit-learn.org/stable/install.html"
                "https://github.com/unit8co/darts/blob/master/INSTALL.md"
            )

        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        model_params.pop("n_forecasts")
        model = LinearRegression(n_jobs=-1)  # n_jobs=-1 indicates to use all processors
        model_params.update({"model": model})  # assign model
        self.model = self.model_class(**model_params)
        self.n_forecasts = self.params["n_forecasts"]
        self.n_lags = model_params["lags"]
        # input checks are provided by model itself

    def fit(self, df: pd.DataFrame, freq: str):
        """Fits the regression model.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally ``ID`` with all data
            freq : str
                frequency of the input data
        """
        self.freq = freq
        series = convert_df_to_TimeSeries(df, value_cols=df.columns.values[1:-1].tolist(), freq=self.freq)
        self.model = self.model.fit(series)

    def predict(self, df: pd.DataFrame, df_historic: pd.DataFrame = None):
        """Runs the model to make predictions.

        Expects all data to be present in dataframe.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally ``ID`` with data
            df_historic : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally ``ID`` with historic data

        Returns
        -------
            pd.DataFrame
                columns ``ds``, ``y``, optionally [``ID``], and [``yhat<i>``] where yhat<i> refers to the
                i-step-ahead prediction for this row's datetime, e.g. yhat3 is the prediction for this datetime,
                predicted 3 steps ago, "3 steps old".
        """
        if df_historic is not None:
            df = self.maybe_add_first_inputs_to_df(df_historic, df)
        df, received_ID_col, received_single_time_series, _ = df_utils.prep_or_copy_df(df)
        # Receives df with single ID column. Only single time series accepted.
        assert received_single_time_series
        value_cols = df.columns.values[1:-1].tolist() if received_single_time_series else df.columns.values[1:].tolist()
        series = convert_df_to_TimeSeries(df, value_cols=value_cols, freq=self.freq)
        predicted_list = self.model.historical_forecasts(
            series,
            start=self.n_lags,
            forecast_horizon=self.n_forecasts,
            retrain=False,
            last_points_only=False,
            verbose=True,
        )
        # convert TimeSeries to np.array
        prediction_series = [prediction_series.values() for i, prediction_series in enumerate(predicted_list)]
        predicted_array = np.stack(prediction_series, axis=0).squeeze()

        fcst_df = self._reshape_raw_predictions_to_forecst_df(df, predicted_array)
        if df_historic is not None:
            fcst_df, df = self.maybe_drop_first_forecasts(fcst_df, df)
        fcst_df, df = self.maybe_drop_added_dates(fcst_df, df)
        return fcst_df

    def _reshape_raw_predictions_to_forecst_df(self, df_i, predicted):  # Todo outsource to df_utils?
        """Turns forecast-origin-wise predictions into forecast-target-wise predictions.
        Parameters
        ----------
            df : pd.DataFrame
                input dataframe
            predicted : np.array
                Array containing the predictions
        Returns
        -------
            pd.DataFrame
                columns ``ds``, ``y``, optionally ``ID`` and [``yhat<i>``],
                Note
                ----
                where yhat<i> refers to the i-step-ahead prediction for this row's datetime.
                e.g. yhat3 is the prediction for this datetime, predicted 3 steps ago, "3 steps old".
        """
        cols = ["ds", "y", "ID"]  # cols to keep from df
        fcst_df = pd.concat((df_i[cols],), axis=1)
        # create a line for each forecast_lag
        # 'yhat<i>' is the forecast for 'y' at 'ds' from i steps ago.
        for forecast_lag in range(1, self.n_forecasts + 1):
            forecast = predicted[:, forecast_lag - 1]
            pad_before = self.n_lags + forecast_lag - 1
            pad_after = self.n_forecasts - forecast_lag
            yhat = np.concatenate(
                ([np.NaN] * pad_before, forecast, [np.NaN] * pad_after)
            )  # add pad based on n_forecasts and current forecast_lag
            name = f"yhat{forecast_lag}"
            fcst_df[name] = yhat

        return fcst_df

    def __handle_missing_data(self, df, freq, predicting):
        """Checks and normalizes new data

        Data is also auto-imputed, since impute_missing is manually set to ``True``.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            predicting : bool
                when no lags, allow NA values in ``y`` of forecast series or ``y`` to miss completely

        Returns
        -------
            pd.DataFrame
                preprocessed dataframe
        """
        # Receives df with single ID column
        assert len(df["ID"].unique()) == 1
        if self.n_lags == 0 and not predicting:
            # we can drop rows with NA in y
            sum_na = sum(df["y"].isna())
            if sum_na > 0:
                df = df[df["y"].notna()]
                log.info(f"dropped {sum_na} NAN row in 'y'")
        # Set impute_missing manually to True
        impute_missing = True

        # add missing dates for autoregression modelling
        if self.n_lags > 0:
            df, missing_dates = df_utils.add_missing_dates_nan(df, freq=freq)
            if missing_dates > 0:
                if impute_missing:
                    log.info(f"{missing_dates} missing dates added.")

        df_end_to_append = None
        nan_at_end = 0
        while len(df) > nan_at_end and df["y"].isnull().iloc[-(1 + nan_at_end)]:
            nan_at_end += 1
        if nan_at_end > 0:
            if predicting:
                # allow nans at end - will re-add at end
                if self.n_forecasts > 1 and self.n_forecasts < nan_at_end:
                    # check that not more than n_forecasts nans, else drop surplus
                    df = df[: -(nan_at_end - self.n_forecasts)]
                    # correct new length:
                    nan_at_end = self.n_forecasts
                    log.info(
                        "Detected y to have more NaN values than n_forecast can predict. "
                        f"Dropped {nan_at_end - self.n_forecasts} rows at end."
                    )
                df_end_to_append = df[-nan_at_end:]
                df = df[:-nan_at_end]
            else:
                # training - drop nans at end
                df = df[:-nan_at_end]
                log.info(
                    f"Dropped {nan_at_end} consecutive nans at end. "
                    "Training data can only be imputed up to last observation."
                )

        # impute missing values
        data_columns = []
        if self.n_lags > 0:
            data_columns.append("y")
        for column in data_columns:
            sum_na = sum(df[column].isnull())
            if sum_na > 0:
                log.warning(f"{sum_na} missing values in column {column} were detected in total. ")
                if impute_missing:
                    # else:
                    df.loc[:, column], remaining_na = df_utils.fill_linear_then_rolling_avg(
                        df[column],
                        limit_linear=10,  # TODO: store in config
                        rolling=10,  # TODO: store in config
                    )
                    log.info(f"{sum_na - remaining_na} NaN values in column {column} were auto-imputed.")
                    if remaining_na > 0:
                        log.warning(
                            f"More than {2 * self.config_missing.impute_linear + self.config_missing.impute_rolling} consecutive missing values encountered in column {column}. "
                            f"{remaining_na} NA remain after auto-imputation. "
                        )
        if df_end_to_append is not None:
            df = pd.concat([df, df_end_to_append])
        return df

    def _handle_missing_data(self, df, freq, predicting=False):
        """Checks and normalizes new data

        Data is also auto-imputed, since impute_missing is manually set to ``True``.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally``ID`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            predicting (bool): when no lags, allow NA values in ``y`` of forecast series or ``y`` to miss completely

        Returns
        -------
            pre-processed df
        """
        df, _, _, _ = df_utils.prep_or_copy_df(df)
        df_handled_missing = pd.DataFrame()
        for df_name, df_i in df.groupby("ID"):
            df_handled_missing_aux = self.__handle_missing_data(df_i, freq, predicting).copy(deep=True)
            df_handled_missing_aux["ID"] = df_name
            df_handled_missing = pd.concat((df_handled_missing, df_handled_missing_aux), ignore_index=True)
        return df_handled_missing
