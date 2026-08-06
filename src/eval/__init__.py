"""
Evaluation framework — scores extraction quality against
analyst ground truth.

Two evaluators share this package:
  * Task-specific: gt_reader / pipeline_reader / aligner / metrics /
    report_writer, driven end-to-end by eval_extraction.py.
  * Generic (any pipeline task): generic_eval.py, with gt_convert.py to
    flatten analyst matrices, matcher_eval.py to validate the matcher
    against human labels, and run_eval_suite.py to run + score all tasks.
"""
