#
#  Copyright (c) 2022 IBM Corp.
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import abc
import logging
import os
import shutil
import threading
import uuid

from collections import defaultdict
from concurrent.futures import Future
from enum import Enum
from typing import Mapping, Sequence, Tuple

import jsonpickle

import label_sleuth.definitions as definitions
from label_sleuth.models.core.languages import Languages, Language
from label_sleuth.models.core.models_background_jobs_manager import ModelsBackgroundJobsManager
from label_sleuth.models.core.prediction import Prediction
from label_sleuth.models.util.disk_cache import load_model_prediction_store_from_disk, \
    save_model_prediction_store_to_disk
from label_sleuth.models.util.LRUCache import LRUCache


PREDICTIONS_STORE_DIR_NAME = "predictions"
LANGUAGE_STR_KEY = "Language"


class ModelStatus(Enum):
    TRAINING = 0
    READY = 1
    ERROR = 2
    DELETED = 3


class ModelAPI(object, metaclass=abc.ABCMeta):
    """
    Base class for implementing a classification model.
    This base class provides general methods for training in the background, caching model predictions etc.,
    while the _train() and _infer() methods are specific to each model implementation.
    """
    def __init__(self, models_background_jobs_manager: ModelsBackgroundJobsManager, gpu_support=False):
        self.models_background_jobs_manager = models_background_jobs_manager
        self.gpu_support = gpu_support
        self.model_locks = defaultdict(lambda: threading.Lock())
        self.cache = LRUCache(definitions.INFER_CACHE_SIZE)
        self.cache_lock = threading.Lock()

    @abc.abstractmethod
    def get_models_dir(self) -> str:
        """
        Returns the base output directory for saving the models and predictions cache.
        """

    @abc.abstractmethod
    def _train(self, model_id: str, train_data: Sequence[Mapping], model_params: Mapping):
        """
        Method for training a classification model on *train_data*. This method is specific to each classification
        model, and is typically launched in the background via the train() method.
        :param model_id: a unique id for the model (which was generated by the train() method)
        :param train_data: a list of dictionaries with at least the "text" and "label" fields, additional fields can be
        passed e.g. [{'text': 'text1', 'label': True, 'additional_field': 'value1'}, {'text': 'text2', 'label': False,
        'additional_field': 'value2'}]
        :param model_params: dictionary for additional model parameters (can be None)
        """

    @abc.abstractmethod
    def _infer(self, model_id, items_to_infer: Sequence[Mapping]) -> Sequence[Prediction]:
        """
        Perform inference using *model_id* on *items_to_infer*, and return the predictions. This method is specific to
        each classification model.
        :param model_id: a unique_id for the model
        :param items_to_infer: a list of dictionaries with at least the "text" field, additional fields can be passed
        e.g. [{'text': 'text1', 'additional_field': 'value1'}, {'text': 'text2', 'additional_field': 'value2'}]
        :return: a list of Prediction objects - one for each item in *items_to_infer* - where Prediction.label is a
        boolean and Prediction.score is a float in the range [0-1].
        If the model returns a non-standard type of prediction object - i.e. one that inherits from the base Prediction
        class and adds additional outputs - it must override the get_predictions_class() method.
        """

    def get_prediction_class(self):
        """
        Returns the prediction dataclass used by the model. This class is used for storing and loading model
        predictions from the disk.
        """
        return Prediction

    def export_model(self, model_id):
        raise NotImplementedError(f"Model '{model_id}' cannot be exported as the 'export_model' method has not been "
                                  f"implemented for {self.__class__.__name__}")

    def train(self, train_data: Sequence[Mapping], language: Language,
              model_params=None, done_callback=None) -> Tuple[str, Future]:
        """
        Create a unique model identifier, and launch a model training job in a background thread.
        :param train_data: a list of dictionaries with at least the "text" and "label" fields, additional fields can be
        passed e.g. [{'text': 'text1', 'label': True, 'additional_field': 'value1'}, {'text': 'text2', 'label': False,
        'additional_field': 'value2'}]
        :param language: the language used to initialize the model. The implemented _train() and _infer() methods can
        then access this parameter via the get_language() call
        :param model_params: dictionary for additional model parameters (can be None)
        :param done_callback: an optional function to be executed once the training job has completed
        :return: a unique identifier for the model, and a Future object for the training job that was submitted in the
        background
        """
        if model_params is None:
            model_params = {}
        model_id = f"{self.__class__.__name__}_{str(uuid.uuid1())}"
        self.mark_train_as_started(model_id)
        self.save_metadata(model_id, language, model_params)

        future = self.models_background_jobs_manager.add_training(model_id, self.train_and_update_status,
                                                                  train_args=(model_id, train_data, model_params),
                                                                  use_gpu=self.gpu_support, done_callback=done_callback)
        return model_id, future

    def train_and_update_status(self, model_id, *args) -> str:
        """
        Run the model _train() function, and return *model_id* if it has finished successfully.
        """
        try:
            self._train(model_id, *args)
            self.mark_train_as_completed(model_id)
        except Exception:
            logging.exception(f'model {model_id} failed with exception')
            self.mark_train_as_error(model_id)
            raise

        return model_id

    def infer(self, model_id, items_to_infer: Sequence[Mapping], use_cache=True) -> Sequence[Prediction]:
        """
        Infer using *model_id* on *items_to_infer*, and return the predictions. This method wraps the model's _infer()
        method which performs the inference itself, adding prediction caching functionality with both an in-memory
        cache and a prediction store on disk.
        Thus, if *use_cache* is True, inference is only performed on items for which there are no predictions in either
        the cache or the disk store, and all predictions for *items_to_infer* are saved to both.

        :param model_id:
        :param items_to_infer: a list of dictionaries with at least the "text" field, additional fields can be passed
        e.g. [{'text': 'text1', 'additional_field': 'value1'}, {'text': 'text2', 'additional_field': 'value2'}]
        :param use_cache: determines whether to use the caching functionality. Default is True
        :return: a list of Prediction objects, one for each item in *items_to_infer*
        """
        if not use_cache:
            logging.info(f"Running infer without cache for {len(items_to_infer)} values in {self.__class__.__name__} "
                         f"model id {model_id}")
            return self._infer(model_id, items_to_infer)

        in_memory_cache_keys = [(model_id, tuple(sorted(item.items()))) for item in items_to_infer]
        model_predictions_store_keys = [tuple(sorted(item.items())) for item in items_to_infer]

        # If there are multiple calls to infer() using the same *model_id*, we do not want them to perform the below
        # logic at the same time. Specifically, if two calls are asking for prediction results for the same element,
        # the desired behavior is that just one of the calls will perform inference (if necessary) and save the
        # prediction results to the cache; after that, the other call can retrieve the results directly from the cache.
        # Thus, we use a lock per model_id.
        with self.model_locks[model_id]:

            # Try to get the predictions from the in-memory cache.
            with self.cache_lock:  # we avoid different threads reading and writing to the cache at the same time
                infer_res = [self.cache.get(cache_key) for cache_key in in_memory_cache_keys]

            indices_not_in_cache = [i for i, v in enumerate(infer_res) if v is None]

            if len(indices_not_in_cache) > 0:  # i.e., some items aren't in the in-memory cache
                logging.info(f"{len(indices_not_in_cache)} not in cache, loading model prediction store from disk "
                             f"in {self.__class__.__name__}")
                model_predictions_store = self._load_model_prediction_store_to_cache(model_id)
                logging.info(f"Done loading model prediction store from disk in {self.__class__.__name__}")
                for idx in indices_not_in_cache:
                    infer_res[idx] = self.cache.get(in_memory_cache_keys[idx])
                indices_not_in_cache = [i for i, v in enumerate(infer_res) if v is None]

            if len(indices_not_in_cache) > 0:  # i.e., some items aren't in the in-memory cache or the prediction store
                logging.info(f"{len(items_to_infer) - len(indices_not_in_cache)} already in cache, running inference "
                             f"for {len(indices_not_in_cache)} values (cache size {self.cache.get_current_size()}) "
                             f"in {self.__class__.__name__}")
                missing_items_to_infer = [items_to_infer[idx] for idx in indices_not_in_cache]
                # If duplicates exist, do not infer the same item more than once
                uniques = set()
                uniques_to_infer = [e for e in missing_items_to_infer if frozenset(e.items()) not in uniques
                                    and not uniques.add(frozenset(e.items()))]

                # Run inference using the model for the missing elements
                new_predictions = self._infer(model_id, uniques_to_infer)
                logging.info(f"finished running infer for {len(indices_not_in_cache)} values")

                item_to_prediction = {frozenset(unique_item.items()): item_predictions
                                      for unique_item, item_predictions in zip(uniques_to_infer, new_predictions)}
                # Update cache and prediction store with predictions for the newly inferred elements
                with self.cache_lock:
                    for idx, entry in zip(indices_not_in_cache, missing_items_to_infer):
                        prediction = item_to_prediction[frozenset(entry.items())]
                        infer_res[idx] = prediction
                        self.cache.set(in_memory_cache_keys[idx], prediction)
                        model_predictions_store[model_predictions_store_keys[idx]] = prediction
                save_model_prediction_store_to_disk(self.get_model_prediction_store_file(model_id),
                                                    model_predictions_store)
            return infer_res

    def infer_async(self, model_id, items_to_infer: Sequence[Mapping], done_callback=None):
        """
        Used for launching an inference job in the background. This method has no return, and is suited for a situation
        where the goal is to run a (potentially) long inference job and cache the results. After this background
        inference is complete, calls to infer() with *model_id* can fetch the prediction results from the cache.

        :param model_id:
        :param items_to_infer: a list of dictionaries with at least the "text" field, additional fields can be passed
        e.g. [{'text': 'text1', 'additional_field': 'value1'}, {'text': 'text2', 'additional_field': 'value2'}]
        :param done_callback: an optional function to be executed once the inference job has completed
        """
        self.models_background_jobs_manager.add_inference(model_id, self.infer, infer_args=(model_id, items_to_infer),
                                                          use_gpu=self.gpu_support, done_callback=done_callback)

    def get_model_dir_by_id(self, model_id):
        return os.path.join(self.get_models_dir(), model_id)

    def get_model_status(self, model_id) -> ModelStatus:
        if os.path.isfile(self.get_completed_flag_path(model_id)):
            return ModelStatus.READY
        elif os.path.isfile(self.get_in_progress_flag_path(model_id)):
            return ModelStatus.TRAINING
        return ModelStatus.ERROR

    def delete_model(self, model_id):
        logging.info(f"Deleting {self.__class__.__name__} model id {model_id}")
        model_dir = self.get_model_dir_by_id(model_id)
        if os.path.isdir(model_dir):
            shutil.rmtree(model_dir)
        prediction_store_path = self.get_model_prediction_store_file(model_id)
        if os.path.exists(prediction_store_path):
            logging.info(f"Deleting prediction store {prediction_store_path}")
            os.remove(prediction_store_path)

    def mark_train_as_started(self, model_id):
        os.makedirs(self.get_model_dir_by_id(model_id), exist_ok=True)
        with open(self.get_in_progress_flag_path(model_id), 'w') as f:
            pass

    def mark_train_as_completed(self, model_id):
        with open(self.get_completed_flag_path(model_id), 'w') as f:
            pass
        os.remove(self.get_in_progress_flag_path(model_id))

    def mark_train_as_error(self, model_id):
        os.remove(self.get_in_progress_flag_path(model_id))

    def get_completed_flag_path(self, model_id):
        return os.path.join(self.get_model_dir_by_id(model_id), f'train_complete_for_{model_id}')

    def get_in_progress_flag_path(self, model_id):
        return os.path.join(self.get_model_dir_by_id(model_id), f'train_in_progress_for_{model_id}')

    def save_metadata(self, model_id, language: Language, model_params: Mapping):
        """
        Saves metadata on the model training parameters (e.g. model language) to disk.
        Specifically, this metadata dictionary should include values for the keys in METADATA_PARAMS_AND_DEFAULTS
        """
        metadata_path = os.path.join(self.get_model_dir_by_id(model_id), 'model_metadata.json')
        model_metadata = {LANGUAGE_STR_KEY: language.name, **model_params}

        with open(metadata_path, 'w') as f:
            f.write(jsonpickle.encode(model_metadata))

    def get_metadata(self, model_id):
        metadata_path = os.path.join(self.get_model_dir_by_id(model_id), 'model_metadata.json')
        with open(metadata_path, 'r') as f:
            metadata = jsonpickle.decode(f.read())
        return metadata

    def get_language(self, model_id) -> Language:
        language_name = self.get_metadata(model_id)[LANGUAGE_STR_KEY]
        return getattr(Languages, language_name.upper())

    def _load_model_prediction_store_to_cache(self, model_id):
        logging.debug("start loading cache from disk")
        model_predictions_store = load_model_prediction_store_from_disk(self.get_model_prediction_store_file(model_id),
                                                                        self.get_prediction_class())
        logging.debug("done loading cache from disk")
        for key, value in model_predictions_store.items():
            self.cache.set((model_id, key), value)
        return model_predictions_store

    def get_model_prediction_store_file(self, model_id):
        return os.path.join(self.get_models_dir(), PREDICTIONS_STORE_DIR_NAME, model_id + ".json")
