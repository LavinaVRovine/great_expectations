import copy
import json
import logging
import os
from copy import deepcopy
from datetime import datetime
from typing import Dict, List, Optional, Union

from great_expectations.checkpoint.configurator import SimpleCheckpointConfigurator
from great_expectations.checkpoint.types.checkpoint_result import CheckpointResult
from great_expectations.checkpoint.util import get_substituted_validation_dict
from great_expectations.core import RunIdentifier
from great_expectations.core.batch import BatchRequest
from great_expectations.core.util import get_datetime_string_from_strftime_format
from great_expectations.data_context.types.base import CheckpointConfig
from great_expectations.data_context.util import substitute_all_config_variables
from great_expectations.exceptions import CheckpointError
from great_expectations.validation_operators import ActionListValidationOperator
from great_expectations.validation_operators.types.validation_operator_result import (
    ValidationOperatorResult,
)
from great_expectations.validator.validator import Validator

logger = logging.getLogger(__name__)


class Checkpoint:
    """
    --ge-feature-maturity-info--

        id: checkpoint
        title: Newstyle Class-based Checkpoints
        short_description: Run a configured checkpoint from a notebook.
        description: Run a configured checkpoint from a notebook.
        how_to_guide_url: https://docs.greatexpectations.io/en/latest/guides/how_to_guides/validation/how_to_create_a_new_checkpoint.html
        maturity: Beta
        maturity_details:
            api_stability: Mostly stable (transitioning ValidationOperators to Checkpoints)
            implementation_completeness: Complete
            unit_test_coverage: Partial ("golden path"-focused tests; error checking tests need to be improved)
            integration_infrastructure_test_coverage: N/A
            documentation_completeness: Complete
            bug_risk: Medium

    --ge-feature-maturity-info--
    """

    def __init__(
        self,
        name: str,
        data_context,
        config_version: Optional[Union[int, float]] = None,
        template_name: Optional[str] = None,
        module_name: Optional[str] = None,
        class_name: Optional[str] = None,
        run_name_template: Optional[str] = None,
        expectation_suite_name: Optional[str] = None,
        batch_request: Optional[Union[BatchRequest, dict]] = None,
        action_list: Optional[List[dict]] = None,
        evaluation_parameters: Optional[dict] = None,
        runtime_configuration: Optional[dict] = None,
        validations: Optional[List[dict]] = None,
        profilers: Optional[List[dict]] = None,
        validation_operator_name: Optional[str] = None,
        batches: Optional[List[dict]] = None,
    ):
        self._name = name
        # Note the gross typechecking to avoid a circular import
        if "DataContext" not in str(type(data_context)):
            raise TypeError("A checkpoint requires a valid DataContext")
        self._data_context = data_context

        checkpoint_config: CheckpointConfig = CheckpointConfig(
            **{
                "name": name,
                "config_version": config_version,
                "template_name": template_name,
                "module_name": module_name,
                "class_name": class_name,
                "run_name_template": run_name_template,
                "expectation_suite_name": expectation_suite_name,
                "batch_request": batch_request,
                "action_list": action_list,
                "evaluation_parameters": evaluation_parameters,
                "runtime_configuration": runtime_configuration,
                "validations": validations,
                "profilers": profilers,
                # Next two fields are for LegacyCheckpoint configuration
                "validation_operator_name": validation_operator_name,
                "batches": batches,
            }
        )
        self._config = checkpoint_config
        self._substituted_config = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def data_context(self):
        return self._data_context

    @property
    def config(self) -> CheckpointConfig:
        return self._config

    @property
    def action_list(self) -> List[Dict]:
        return self._config.action_list

    # TODO: (Rob) should we type the big validation dicts for better validation/prevent duplication
    def get_substituted_config(
        self,
        config: Optional[Union[CheckpointConfig, dict]] = None,
        runtime_kwargs: Optional[dict] = None,
    ) -> CheckpointConfig:
        runtime_kwargs = runtime_kwargs or {}
        if config is None:
            config = self.config
        if isinstance(config, dict):
            config = CheckpointConfig(**config)

        if (
            self._substituted_config is not None
            and not runtime_kwargs.get("template_name")
            and not config.template_name
        ):
            substituted_config = deepcopy(self._substituted_config)
            if any(runtime_kwargs.values()):
                substituted_config.update(runtime_kwargs=runtime_kwargs)
        else:
            template_name = runtime_kwargs.get("template_name") or config.template_name

            if not template_name:
                substituted_config = copy.deepcopy(config)
                if any(runtime_kwargs.values()):
                    substituted_config.update(runtime_kwargs=runtime_kwargs)

                self._substituted_config = substituted_config
            else:
                checkpoint = self.data_context.get_checkpoint(name=template_name)
                template_config = checkpoint.config

                if template_config.config_version != config.config_version:
                    raise CheckpointError(
                        f"Invalid template '{template_name}' (ver. {template_config.config_version}) for Checkpoint "
                        f"'{config}' (ver. {config.config_version}. Checkpoints can only use templates with the same config_version."
                    )

                if template_config.template_name is not None:
                    substituted_config = self.get_substituted_config(
                        config=template_config
                    )
                else:
                    substituted_config = template_config

                # merge template with config
                substituted_config.update(
                    other_config=config, runtime_kwargs=runtime_kwargs
                )

                # don't replace _substituted_config if already exists
                if self._substituted_config is None:
                    self._substituted_config = substituted_config
        return self._substitute_config_variables(config=substituted_config)

    def _substitute_config_variables(
        self, config: CheckpointConfig
    ) -> CheckpointConfig:
        substituted_config_variables = substitute_all_config_variables(
            self.data_context.config_variables,
            dict(os.environ),
            self.data_context.DOLLAR_SIGN_ESCAPE_STRING,
        )

        substitutions = {
            **substituted_config_variables,
            **dict(os.environ),
            **self.data_context.runtime_environment,
        }

        return CheckpointConfig(
            **substitute_all_config_variables(
                config, substitutions, self.data_context.DOLLAR_SIGN_ESCAPE_STRING
            )
        )

    # TODO: Add eval param processing using new TBD parser syntax and updated EvaluationParameterParser and
    #  parse_evaluation_parameters function (e.g. datetime substitution or specifying relative datetimes like "most
    #  recent"). Currently, environment variable substitution is the only processing applied to evaluation parameters,
    # while run_name_template also undergoes strftime datetime substitution
    def run(
        self,
        template_name: Optional[str] = None,
        run_name_template: Optional[str] = None,
        expectation_suite_name: Optional[str] = None,
        batch_request: Optional[Union[BatchRequest, dict]] = None,
        action_list: Optional[List[dict]] = None,
        evaluation_parameters: Optional[dict] = None,
        runtime_configuration: Optional[dict] = None,
        validations: Optional[List[dict]] = None,
        profilers: Optional[List[dict]] = None,
        run_id=None,
        run_name=None,
        run_time=None,
        result_format=None,
        **kwargs,
    ) -> CheckpointResult:
        assert not (run_id and run_name) and not (
            run_id and run_time
        ), "Please provide either a run_id or run_name and/or run_time."

        run_time = run_time or datetime.now()
        runtime_configuration: dict = runtime_configuration or {}
        result_format: Optional[dict] = result_format or runtime_configuration.get(
            "result_format"
        )
        if result_format is None:
            result_format = {"result_format": "SUMMARY"}

        runtime_kwargs = {
            "template_name": template_name,
            "run_name_template": run_name_template,
            "expectation_suite_name": expectation_suite_name,
            "batch_request": batch_request,
            "action_list": action_list,
            "evaluation_parameters": evaluation_parameters,
            "runtime_configuration": runtime_configuration,
            "validations": validations,
            "profilers": profilers,
        }
        substituted_runtime_config: CheckpointConfig = self.get_substituted_config(
            runtime_kwargs=runtime_kwargs
        )
        run_name_template: Optional[str] = substituted_runtime_config.run_name_template
        validations: list = substituted_runtime_config.validations
        run_results = {}

        if run_name is None and run_name_template is not None:
            run_name: str = get_datetime_string_from_strftime_format(
                format_str=run_name_template, datetime_obj=run_time
            )

        run_id = run_id or RunIdentifier(run_name=run_name, run_time=run_time)

        for idx, validation_dict in enumerate(validations):
            try:
                substituted_validation_dict: dict = get_substituted_validation_dict(
                    substituted_runtime_config=substituted_runtime_config,
                    validation_dict=validation_dict,
                )
                batch_request: BatchRequest = substituted_validation_dict.get(
                    "batch_request"
                )
                expectation_suite_name: str = substituted_validation_dict.get(
                    "expectation_suite_name"
                )
                action_list: list = substituted_validation_dict.get("action_list")

                validator: Validator = self.data_context.get_validator(
                    batch_request=batch_request,
                    expectation_suite_name=expectation_suite_name,
                )
                action_list_validation_operator: ActionListValidationOperator = (
                    ActionListValidationOperator(
                        data_context=self.data_context,
                        action_list=action_list,
                        result_format=result_format,
                        name=f"{self.name}-checkpoint-validation[{idx}]",
                    )
                )
                val_op_run_result: ValidationOperatorResult = (
                    action_list_validation_operator.run(
                        assets_to_validate=[validator],
                        run_id=run_id,
                        evaluation_parameters=substituted_validation_dict.get(
                            "evaluation_parameters"
                        ),
                        result_format=result_format,
                    )
                )
                run_results.update(val_op_run_result.run_results)
            except CheckpointError as e:
                raise CheckpointError(
                    f"Exception occurred while running validation[{idx}] of checkpoint '{self.name}': {e.message}"
                )
        return CheckpointResult(
            run_id=run_id, run_results=run_results, checkpoint_config=self.config
        )

    def self_check(self, pretty_print=True) -> dict:
        # Provide visibility into parameters that Checkpoint was instantiated with.
        report_object: dict = {"config": self.config.to_json_dict()}

        if pretty_print:
            print(f"\nCheckpoint class name: {self.__class__.__name__}")

        validations_present: bool = (
            self.config.validations
            and isinstance(self.config.validations, list)
            and len(self.config.validations) > 0
        )
        action_list: Optional[list] = self.config.action_list
        action_list_present: bool = (
            action_list is not None
            and isinstance(action_list, list)
            and len(action_list) > 0
        ) or (
            validations_present
            and all(
                [
                    (
                        validation.get("action_list")
                        and isinstance(validation["action_list"], list)
                        and len(validation["action_list"]) > 0
                    )
                    for validation in self.config.validations
                ]
            )
        )
        if pretty_print:
            if not validations_present:
                print(
                    f"""Your current Checkpoint configuration has an empty or missing "validations" attribute.  This
means you must either update your checkpoint configuration or provide an appropriate validations
list programmatically (i.e., when your Checkpoint is run).
                    """
                )
            if not action_list_present:
                print(
                    f"""Your current Checkpoint configuration has an empty or missing "action_list" attribute.  This
means you must provide an appropriate validations list programmatically (i.e., when your Checkpoint
is run), with each validation having its own defined "action_list" attribute.
                    """
                )

        return report_object


class LegacyCheckpoint(Checkpoint):
    """
    --ge-feature-maturity-info--

        id: checkpoint_notebook
        title: LegacyCheckpoint - Notebook
        icon:
        short_description: Run a configured checkpoint from a notebook.
        description: Run a configured checkpoint from a notebook.
        how_to_guide_url: https://docs.greatexpectations.io/en/latest/guides/how_to_guides/validation/how_to_run_a_checkpoint_in_python.html
        maturity: Experimental (to-be-deprecated in favor of Checkpoint)
        maturity_details:
            api_stability: to-be-deprecated in favor of Checkpoint
            implementation_completeness: Complete
            unit_test_coverage: Partial ("golden path"-focused tests; error checking tests need to be improved)
            integration_infrastructure_test_coverage: N/A
            documentation_completeness: Complete
            bug_risk: Low

        id: checkpoint_command_line
        title: LegacyCheckpoint - Command Line
        icon:
        short_description: Run a configured checkpoint from a command line.
        description: Run a configured checkpoint from a command line in a Terminal shell.
        how_to_guide_url: https://docs.greatexpectations.io/en/latest/guides/how_to_guides/validation/how_to_run_a_checkpoint_in_terminal.html
        maturity: Experimental (to-be-deprecated in favor of Checkpoint)
        maturity_details:
            api_stability: to-be-deprecated in favor of Checkpoint
            implementation_completeness: Complete
            unit_test_coverage: Complete
            integration_infrastructure_test_coverage: N/A
            documentation_completeness: Complete
            bug_risk: Low

        id: checkpoint_cron_job
        title: LegacyCheckpoint - Cron
        icon:
        short_description: Deploy a configured checkpoint as a scheduled task with cron.
        description: Use the Unix crontab command to edit the cron file and add a line that will run checkpoint as a scheduled task.
        how_to_guide_url: https://docs.greatexpectations.io/en/latest/guides/how_to_guides/validation/how_to_deploy_a_scheduled_checkpoint_with_cron.html
        maturity: Experimental (to-be-deprecated in favor of Checkpoint)
        maturity_details:
            api_stability: to-be-deprecated in favor of Checkpoint
            implementation_completeness: Complete
            unit_test_coverage: Complete
            integration_infrastructure_test_coverage: N/A
            documentation_completeness: Complete
            bug_risk: Low

        id: checkpoint_airflow_dag
        title: LegacyCheckpoint - Airflow DAG
        icon:
        short_description: Run a configured checkpoint in Apache Airflow
        description: Running a configured checkpoint in Apache Airflow enables the triggering of data validation using an Expectation Suite directly within an Airflow DAG.
        how_to_guide_url: https://docs.greatexpectations.io/en/latest/guides/how_to_guides/validation/how_to_run_a_checkpoint_in_airflow.html
        maturity: Beta (to-be-deprecated in favor of Checkpoint)
        maturity_details:
            api_stability: to-be-deprecated in favor of Checkpoint
            implementation_completeness: Partial (no operator, but probably don't need one)
            unit_test_coverage: N/A
            integration_infrastructure_test_coverage: Minimal
            documentation_completeness: Complete (pending how-to)
            bug_risk: Low

        id: checkpoint_kedro
        title: LegacyCheckpoint - Kedro
        icon:
        short_description:
        description:
        how_to_guide_url:
        maturity: Experimental (to-be-deprecated in favor of Checkpoint)
        maturity_details:
            api_stability: to-be-deprecated in favor of Checkpoint
            implementation_completeness: Unknown
            unit_test_coverage: Unknown
            integration_infrastructure_test_coverage: Unknown
            documentation_completeness:  Minimal (none)
            bug_risk: Unknown

        id: checkpoint_prefect
        title: LegacyCheckpoint - Prefect
        icon:
        short_description:
        description:
        how_to_guide_url:
        maturity: Experimental (to-be-deprecated in favor of Checkpoint)
        maturity_details:
            api_stability: to-be-deprecated in favor of Checkpoint
            implementation_completeness: Unknown
            unit_test_coverage: Unknown
            integration_infrastructure_test_coverage: Unknown
            documentation_completeness: Minimal (none)
            bug_risk: Unknown

        id: checkpoint_dbt
        title: LegacyCheckpoint - DBT
        icon:
        short_description:
        description:
        how_to_guide_url:
        maturity: Beta (to-be-deprecated in favor of Checkpoint)
        maturity_details:
            api_stability: to-be-deprecated in favor of Checkpoint
            implementation_completeness: Minimal
            unit_test_coverage: Minimal (none)
            integration_infrastructure_test_coverage: Minimal (none)
            documentation_completeness: Minimal (none)
            bug_risk: Low

    --ge-feature-maturity-info--
    """

    def __init__(
        self,
        name: str,
        data_context,
        validation_operator_name: Optional[str] = None,
        batches: Optional[List[dict]] = None,
    ):
        super().__init__(
            name=name,
            data_context=data_context,
            validation_operator_name=validation_operator_name,
            batches=batches,
        )

    @property
    def validation_operator_name(self):
        return self.config.validation_operator_name

    @property
    def batches(self):
        return self.config.batches

    def run(
        self,
        run_id=None,
        evaluation_parameters=None,
        run_name=None,
        run_time=None,
        result_format=None,
        **kwargs,
    ):
        batches_to_validate = self._get_batches_to_validate(self.batches)

        results = self.data_context.run_validation_operator(
            self.validation_operator_name,
            assets_to_validate=batches_to_validate,
            run_id=run_id,
            evaluation_parameters=evaluation_parameters,
            run_name=run_name,
            run_time=run_time,
            result_format=result_format,
            **kwargs,
        )

        return results

    def get_config(self, format="dict"):
        if format == "dict":
            return self.config.to_json_dict()

        elif format == "yaml":
            return self.config.to_yaml_str()

        else:
            raise ValueError(f"Unknown format {format} in LegacyCheckpoint.get_config.")

    def _get_batches_to_validate(self, batches):
        batches_to_validate = []
        for batch in batches:

            batch_kwargs = batch["batch_kwargs"]
            suites = batch["expectation_suite_names"]

            if not suites:
                raise Exception(
                    f"""A batch has no suites associated with it. At least one suite is required.
    - Batch: {json.dumps(batch_kwargs)}
    - Please add at least one suite to checkpoint {self.name}
"""
                )

            for suite_name in batch["expectation_suite_names"]:
                suite = self.data_context.get_expectation_suite(suite_name)
                batch = self.data_context.get_batch(batch_kwargs, suite)

                batches_to_validate.append(batch)

        return batches_to_validate


class SimpleCheckpoint(Checkpoint):
    _configurator_class = SimpleCheckpointConfigurator

    def __init__(
        self,
        name: str,
        data_context,
        config_version: Optional[Union[int, float]] = None,
        template_name: Optional[str] = None,
        module_name: Optional[str] = None,
        class_name: Optional[str] = None,
        run_name_template: Optional[str] = None,
        expectation_suite_name: Optional[str] = None,
        batch_request: Optional[Union[BatchRequest, dict]] = None,
        action_list: Optional[List[dict]] = None,
        evaluation_parameters: Optional[dict] = None,
        runtime_configuration: Optional[dict] = None,
        validations: Optional[List[dict]] = None,
        profilers: Optional[List[dict]] = None,
        validation_operator_name: Optional[str] = None,
        batches: Optional[List[dict]] = None,
        # the following four arguments are used by SimpleCheckpointConfigurator
        site_names: Optional[Union[str, List[str]]] = "all",
        slack_webhook: Optional[str] = None,
        notify_on: Optional[str] = "all",
        notify_with: Optional[Union[str, List[str]]] = "all",
        **kwargs,
    ):
        checkpoint_config: CheckpointConfig = self._configurator_class(
            name=name,
            data_context=data_context,
            config_version=config_version,
            template_name=template_name,
            class_name=class_name,
            module_name=module_name,
            run_name_template=run_name_template,
            expectation_suite_name=expectation_suite_name,
            batch_request=batch_request,
            action_list=action_list,
            evaluation_parameters=evaluation_parameters,
            runtime_configuration=runtime_configuration,
            validations=validations,
            profilers=profilers,
            site_names=site_names,
            slack_webhook=slack_webhook,
            notify_on=notify_on,
            notify_with=notify_with,
        ).build()

        super().__init__(
            name=checkpoint_config.name,
            data_context=data_context,
            config_version=checkpoint_config.config_version,
            template_name=checkpoint_config.template_name,
            module_name=checkpoint_config.module_name,
            class_name=checkpoint_config.class_name,
            run_name_template=checkpoint_config.run_name_template,
            expectation_suite_name=checkpoint_config.expectation_suite_name,
            batch_request=checkpoint_config.batch_request,
            action_list=checkpoint_config.action_list,
            evaluation_parameters=checkpoint_config.evaluation_parameters,
            runtime_configuration=checkpoint_config.runtime_configuration,
            validations=checkpoint_config.validations,
            profilers=checkpoint_config.profilers,
        )