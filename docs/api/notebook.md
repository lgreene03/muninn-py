# Notebook Helpers

Pure-function helpers for the most common research operations over a Muninn feature panel.
All functions take Polars DataFrames and return Polars DataFrames — no mutation, no wall-clock reads.

!!! note
    These helpers require `polars`. Install the notebook extras to get polars plus
    plotting dependencies:
    ```bash
    pip install "muninn-py[notebooks]"
    ```

::: muninn.notebook.forward_returns

::: muninn.notebook.information_coefficient

::: muninn.notebook.rolling_corr

::: muninn.notebook.hit_rate
