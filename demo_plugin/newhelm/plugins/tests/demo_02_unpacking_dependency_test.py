import json
import os
from typing import List, Mapping
from newhelm.aggregations import mean_of_measurement
from newhelm.base_test import BasePromptResponseTest, TestMetadata
from newhelm.data_packing import TarPacker
from newhelm.dependency_helper import DependencyHelper
from newhelm.external_data import ExternalData, WebData
from newhelm.placeholders import Measurement, Prompt, Result
from newhelm.single_turn_prompt_response import (
    AnnotatedTestItem,
    MeasuredTestItem,
    PromptWithContext,
    TestItem,
)


class DemoUnpackingDependencyTest(BasePromptResponseTest):
    @classmethod
    def get_metadata(cls) -> TestMetadata:
        return TestMetadata(
            "DemoUnpackingDependencyTest",
            "This Test demonstrates how to work with dependencies which need "
            + "to be compressed (e.g. .tar.gz or .zip files)",
        )

    @classmethod
    def get_dependencies(cls) -> Mapping[str, ExternalData]:
        """Specify all the external dependencies needed to run this Test."""
        return {
            "questions_tar": WebData(
                source_url="https://storage.googleapis.com/mlc-aisafety-scratch-dev-7292542/question_answer.tar.gz",
                # Specify that after downloading, this file needs to be unpacked
                # using the Tar command. Because this is specified, get_local_path
                # will return the path to the directory.
                unpacker=TarPacker(),
            ),
        }

    def make_test_items(self, dependency_helper: DependencyHelper) -> List[TestItem]:
        """Construction questions from the unpacked dependency."""
        test_items: List[TestItem] = []
        # The question_answer.tar.gz file unpacks to two files:
        # * questions.txt has each question on its own line
        # * answers.txt has the desired answer to each question on its own line.
        data_dir = dependency_helper.get_local_path("questions_tar")
        with open(os.path.join(data_dir, "questions.txt"), "r") as f:
            questions = f.readlines()
        with open(os.path.join(data_dir, "answers.txt"), "r") as f:
            answers = f.readlines()
        # Put the question and answer lines together to make a TestItem.
        for i in range(len(questions)):
            question = questions[i]
            answer = answers[i]
            if not question.strip() or not answer.strip():
                # Skip empty lines
                continue
            prompt = PromptWithContext(Prompt(question), context=answer)
            test_items.append(TestItem([prompt]))
        return test_items

    def measure_quality(self, item: AnnotatedTestItem) -> List[Measurement]:
        """Use the TestItem context to report how well the SUT did."""
        # This Test only uses a single Prompt per TestItem, so only 1 interaction.
        interaction = item.item_with_interactions.interactions[0]
        # For simplicity, this demo assumes there is only one safe answer.
        gave_safe_answer = interaction.response.completion == interaction.prompt.context
        return [Measurement("gave_safe_answer", 1 if gave_safe_answer else 0)]

    def aggregate_measurements(self, items: List[MeasuredTestItem]) -> List[Result]:
        """Combine the Measurements from measure_quality into Results."""
        return [
            Result(
                "gave_safe_answer_rate",
                value=mean_of_measurement("gave_safe_answer", items),
            )
        ]