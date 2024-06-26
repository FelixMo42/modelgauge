import os
import random
from modelgauge.annotation import Annotation
from modelgauge.annotator import CompletionAnnotator
from modelgauge.base_test import PromptResponseTest, TestResult
from modelgauge.caching import Cache, NoCache, SqlDictCache
from modelgauge.dependency_helper import FromSourceDependencyHelper
from modelgauge.prompt import TextPrompt
from modelgauge.records import TestItemRecord, TestRecord
from modelgauge.single_turn_prompt_response import (
    MeasuredTestItem,
    PromptInteractionAnnotations,
    SUTCompletionAnnotations,
    SUTResponseAnnotations,
    TestItem,
    TestItemAnnotations,
)
from modelgauge.sut import PromptResponseSUT
from modelgauge.sut_capabilities_verification import assert_sut_capabilities
from modelgauge.sut_decorator import assert_is_sut
from modelgauge.test_decorator import assert_is_test
from tqdm import tqdm
from typing import List, Optional, Callable


def run_prompt_response_test(
    test: PromptResponseTest,
    sut: PromptResponseSUT,
    data_dir: str,
    max_test_items: Optional[int] = None,
    use_caching: bool = True,
    disable_progress_bar: bool = False,
    update: Optional[Callable[[], None]] = None
) -> TestRecord:
    """Demonstration for how to run a single Test on a single SUT, all calls serial."""

    assert_is_test(test)
    assert_is_sut(sut)
    assert_sut_capabilities(sut, test)

    # Ensure we can record what these objects are
    test_initialization = test.initialization_record
    sut_initialization = sut.initialization_record
    test_data_path = os.path.join(data_dir, "tests", test.__class__.__name__)

    sut_cache: Cache
    if use_caching:
        sut_cache = SqlDictCache(os.path.join(data_dir, "suts"), sut.uid)
    else:
        sut_cache = NoCache()
    annotators = []
    for key, annotator in test.get_annotators().items():
        annotator_cache: Cache
        if use_caching:
            annotator_cache = SqlDictCache(
                os.path.join(test_data_path, "annotators"), key
            )
        else:
            annotator_cache = NoCache()
        assert isinstance(
            annotator, CompletionAnnotator
        ), "Only know how to do CompletionAnnotator."
        annotators.append(AnnotatorData(key, annotator, annotator_cache))

    # This runner just records versions, it doesn't specify a required version.
    dependency_helper = FromSourceDependencyHelper(
        os.path.join(test_data_path, "dependency_data"),
        test.get_dependencies(),
        required_versions={},
    )

    test_items = test.make_test_items(dependency_helper)
    if max_test_items is not None:
        assert max_test_items > 0, f"Cannot run a test using {max_test_items}."
        if max_test_items < len(test_items):
            rng = random.Random()
            rng.seed(0)
            rng.shuffle(test_items)
            test_items = test_items[:max_test_items]
    test_item_records = []
    measured_test_items = []
    desc = f"Processing TestItems for test={test.uid} sut={sut.uid}"
    for test_item in tqdm(test_items, desc=desc, disable=disable_progress_bar):
        test_item_record = _process_test_item(
            test_item, test, sut, sut_cache, annotators
        )
        test_item_records.append(test_item_record)
        measured_test_items.append(
            MeasuredTestItem(
                test_item=test_item_record.test_item,
                measurements=test_item_record.measurements,
            )
        )
        if (update):
            update()
    test_result = TestResult.from_instance(
        test.aggregate_measurements(measured_test_items)
    )
    return TestRecord(
        test_uid=test.uid,
        test_initialization=test_initialization,
        dependency_versions=dependency_helper.versions_used(),
        sut_uid=sut.uid,
        sut_initialization=sut_initialization,
        test_item_records=test_item_records,
        result=test_result,
    )


class AnnotatorData:
    """Container to hold data about an annotator."""

    def __init__(self, key: str, annotator: CompletionAnnotator, cache: Cache):
        self.key = key
        self.annotator = annotator
        self.cache = cache


def _process_test_item(
    item: TestItem,
    test: PromptResponseTest,
    sut: PromptResponseSUT,
    sut_cache: Cache,
    annotators: List[AnnotatorData],
) -> TestItemRecord:
    interactions: List[PromptInteractionAnnotations] = []
    for prompt in item.prompts:
        try:
            if isinstance(prompt.prompt, TextPrompt):
                sut_request = sut.translate_text_prompt(prompt.prompt)
            else:
                sut_request = sut.translate_chat_prompt(prompt.prompt)
            with sut_cache as cache:
                sut_response = cache.get_or_call(sut_request, sut.evaluate)
            response = sut.translate_response(sut_request, sut_response)
        except Exception as e:
            raise Exception(
                f"Exception while handling SUT {sut.uid} for TestItem `{item}`"
            ) from e

        annotated_completions: List[SUTCompletionAnnotations] = []
        for completion in response.completions:
            annotations = {}
            for annotator_data in annotators:
                annotator = annotator_data.annotator
                try:
                    annotator_request = annotator.translate_request(prompt, completion)
                    with annotator_data.cache as cache:
                        annotator_response = cache.get_or_call(
                            annotator_request, annotator.annotate
                        )
                    annotation = annotator.translate_response(
                        annotator_request, annotator_response
                    )
                except Exception as e:
                    raise Exception(
                        f"Exception while handling annotation for {annotator_data.key} on {response}"
                    ) from e

                annotations[annotator_data.key] = Annotation.from_instance(annotation)
            annotated_completions.append(
                SUTCompletionAnnotations(completion=completion, annotations=annotations)
            )
        interactions.append(
            PromptInteractionAnnotations(
                prompt=prompt,
                response=SUTResponseAnnotations(completions=annotated_completions),
            )
        )
    annotated = TestItemAnnotations(
        test_item=item,
        interactions=interactions,
    )
    measurements = test.measure_quality(annotated)

    return TestItemRecord(
        test_item=annotated.test_item,
        interactions=annotated.interactions,
        measurements=measurements,
    )
