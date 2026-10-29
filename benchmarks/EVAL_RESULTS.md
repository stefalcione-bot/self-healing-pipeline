### Benchmark Evaluation Results

- **Pass@1 Recovery Rate:** `100.0%`
- **Pass@3 Recovery Rate:** `100.0%`
- **Average Recovery Latency:** `2.57s`

| Scenario ID | Category | Status | Attempts | Latency |
| :--- | :--- | :---: | :---: | :---: |
| `case_01_single_rename` | RENAME | PASS | 1 | 1.85s |
| `case_02_multi_rename` | RENAME | PASS | 1 | 1.85s |
| `case_03_camelcase_drift` | RENAME | PASS | 1 | 1.85s |
| `case_04_rename_with_noise_cols` | RENAME | PASS | 1 | 1.85s |
| `case_05_string_types` | TYPE_CAST | PASS | 1 | 1.85s |
| `case_06_int_user_id_and_decimal` | TYPE_CAST | PASS | 1 | 1.85s |
| `case_07_boolean_as_int_flag` | TYPE_CAST | PASS | 1 | 1.85s |
| `case_08_slash_date_format` | TYPE_CAST | PASS | 1 | 1.85s |
| `case_09_cents_instead_of_dollars` | UNIT_SHIFT | PASS | 1 | 1.85s |
| `case_10_grams_instead_of_kg` | UNIT_SHIFT | PASS | 1 | 1.85s |
| `case_11_ms_instead_of_seconds` | UNIT_SHIFT | PASS | 1 | 1.85s |
| `case_12_thousands_scale_shift` | UNIT_SHIFT | PASS | 1 | 1.85s |
| `case_13_dollar_sign_prefix` | DATA_CLEANING | PASS | 1 | 1.85s |
| `case_14_unit_suffix_strings` | DATA_CLEANING | PASS | 1 | 1.85s |
| `case_15_null_spike_imputation` | DATA_CLEANING | PASS | 1 | 1.85s |
| `case_16_negative_amount_glitch` | DATA_CLEANING | PASS | 1 | 1.85s |
| `case_17_rename_plus_cents_plus_date_cast` | COMPOUND | PASS | 1 | 1.85s |
| `case_18_shipments_grams_ms_rename` | COMPOUND | PASS | 1 | 1.85s |
| `case_19_currency_string_plus_rename` | COMPOUND | PASS | 1 | 2.3s |
| `case_20_full_chaos_erp_export` | COMPOUND | PASS | 1 | 15.82s |