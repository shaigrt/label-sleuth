import os
import random
import tempfile
import unittest
from unittest.mock import patch

from lrtc_lib.active_learning.core.active_learning_factory import ActiveLearningFactory
from lrtc_lib.config import CONFIGURATION
from lrtc_lib.data_access.core.data_structs import Document, LABEL_POSITIVE, Label, LABEL_NEGATIVE, DisplayFields
from lrtc_lib.data_access.file_based.file_based_data_access import FileBasedDataAccess
from lrtc_lib.data_access.test_file_based_data_access import generate_corpus
from lrtc_lib.models.core.models_background_jobs_manager import ModelsBackgroundJobsManager
from lrtc_lib.models.core.models_factory import ModelFactory
from lrtc_lib.orchestrator.core.state_api.orchestrator_state_api import OrchestratorStateApi
from lrtc_lib.orchestrator.orchestrator_api import OrchestratorApi
from lrtc_lib.training_set_selector.training_set_selector_factory import get_training_set_selector


def add_random_labels_to_document(doc: Document, min_num_sentences_to_label: int, categories, seed=0):
    random.seed(seed)
    text_elements_to_label = random.sample(doc.text_elements, min(min_num_sentences_to_label, len(doc.text_elements)))
    for elem in text_elements_to_label:
        categories_to_label = random.sample(categories, random.randint(0, len(categories)))
        elem.category_to_label = \
            {cat: Label(label=LABEL_POSITIVE) if cat in categories_to_label else Label(label=LABEL_NEGATIVE)
             for cat in categories}
    return text_elements_to_label


class TestOrchestratorAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.model_factory =  ModelFactory(ModelsBackgroundJobsManager())
        cls.active_learning_factory = ActiveLearningFactory()
        cls.data_access = FileBasedDataAccess(os.path.join(cls.temp_dir.name, "output"))
        cls.orchestrator_state = OrchestratorStateApi(os.path.join(cls.temp_dir.name, "output", "workspaces"))
        cls.orchestrator_api = OrchestratorApi(cls.orchestrator_state, cls.data_access, cls.active_learning_factory,
                                               cls.model_factory, CONFIGURATION)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_export_and_import_workspace_labels(self):
        dataset_name = self.test_export_and_import_workspace_labels.__name__ + '_dump'
        docs = generate_corpus(self.data_access, dataset_name)
        categories = ['cat_' + str(i) for i in range(3)]

        self.orchestrator_api.create_workspace(workspace_id='mock_workspace', dataset_name=dataset_name)
        for cat in categories:
            self.orchestrator_api.create_new_category('mock_workspace', cat, 'some_description')
        labeled_elements_for_export = add_random_labels_to_document(docs[0], 5, categories)

        # use export_workspace_labels() to turn labeled_elements into a dataframe for export
        with patch.object(FileBasedDataAccess, 'get_labeled_text_elements') as mock_get_labeled_elements_func:
            mock_get_labeled_elements_func.return_value = {'results': labeled_elements_for_export}
            exported_df = self.orchestrator_api.export_workspace_labels('mock_workspace')

        for column in exported_df.columns:
            self.assertIn(column, DisplayFields.__dict__.values())

        # import the resulting dataframe using import_workspace_labels()
        self.orchestrator_state.create_workspace(workspace_id='mock_workspace_2', dataset_name=dataset_name)
        self.orchestrator_api.import_category_labels('mock_workspace_2', exported_df)

        unique = set()
        labeled_elements_imported = \
            [element for cat in categories for element
             in self.orchestrator_api.get_all_labeled_text_elements('mock_workspace_2', dataset_name, cat)
             if element.uri not in unique and not unique.add(element.uri)]

        self.assertEqual(sorted(labeled_elements_for_export, key=lambda te: te.uri),
                         sorted(labeled_elements_imported, key=lambda te: te.uri))

    @patch.object(OrchestratorApi, 'run_iteration')
    @patch.object(OrchestratorStateApi, 'get_label_change_count_since_last_train')
    @patch.object(FileBasedDataAccess, 'get_label_counts')
    def test_train_if_recommended(self, mock_get_label_counts, mock_get_label_change_count, mock_run_iteration):
        workspace_id = self.test_train_if_recommended.__name__
        dataset_name = f'{workspace_id}_dump'
        category_name = f'{workspace_id}_cat'
        generate_corpus(self.data_access, dataset_name)
        self.orchestrator_api.create_workspace(workspace_id, dataset_name)
        self.orchestrator_api.create_new_category(workspace_id, category_name, 'some_description')

        change_threshold = self.orchestrator_api.config.changed_element_threshold
        first_model_pos_threshold = self.orchestrator_api.config.first_model_positive_threshold

        # do not trigger training
        negative_count = 1
        positive_count = min(max(0, change_threshold - negative_count - 1), max(0, first_model_pos_threshold - 1))

        label_counts = {LABEL_POSITIVE: positive_count, LABEL_NEGATIVE: negative_count}
        mock_get_label_counts.return_value = label_counts
        mock_get_label_change_count.return_value = sum(label_counts.values())

        self.orchestrator_api.train_if_recommended(workspace_id, category_name)
        mock_run_iteration.assert_not_called()

        # trigger training
        positive_count = max(change_threshold, first_model_pos_threshold) - negative_count + 1
        label_counts = {LABEL_POSITIVE: positive_count, LABEL_NEGATIVE: negative_count}
        mock_get_label_counts.return_value = label_counts
        mock_get_label_change_count.return_value = sum(label_counts.values())

        train_set_selector_cls = \
            get_training_set_selector(self.orchestrator_api.config.training_set_selection_strategy).__class__
        with patch.object(train_set_selector_cls, 'get_train_set'):
            self.orchestrator_api.train_if_recommended(workspace_id, category_name)
        mock_run_iteration.assert_called()
