import pandas as pd
from google.cloud import bigquery
from google.api_core import retry
import time

# Retry predicate that covers the transient 503 "Visibility check was unavailable" errors
# returned by BigQuery's job polling endpoint.
_BQ_RETRY = retry.Retry(predicate=retry.if_transient_error)

# Module-level client — constructed once, reused across all calls (thread-safe for reads;
# parallel materialize_view calls each submit their own job and wait independently).
_client: bigquery.Client | None = None

def _get_client(project_id: str) -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=project_id)
    return _client

def append_to_bigquery(data, table_id, dataset_id='home_afm', project_id='ecstatic-pod-443723-f6'):
    """
    Appends a pandas DataFrame or CSV file to an existing BigQuery table.

    Parameters:
        data (pd.DataFrame or str): A pandas DataFrame or path to a CSV file.
        dataset_id (str): The ID of the dataset in BigQuery.
        table_id (str): The ID of the table in BigQuery.
        project_id (str): The GCP project ID.
    """
    if isinstance(data, str):
        df = pd.read_csv(data)
    elif isinstance(data, pd.DataFrame):
        df = data
    else:
        raise ValueError("Input must be a pandas DataFrame or path to a CSV file.")

    client = _get_client(project_id)
    table_ref = client.dataset(dataset_id).table(table_id)

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        autodetect=True
    )

    start_time = time.time()
    print(f"[bq] Appending {len(df)} rows to {dataset_id}.{table_id} ...")
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result(retry=_BQ_RETRY, timeout=300)  # Waits for the job to complete; retries on transient 503s
    elapsed_time = time.time() - start_time

    print(f"[bq] Appended {len(df)} rows to {dataset_id}.{table_id} in {elapsed_time:.2f}s")

def materialize_view(view_name, destination_table, dataset_id='home_afm', project_id='ecstatic-pod-443723-f6'):
    """
    Materializes a BigQuery view into a table by running CREATE OR REPLACE TABLE ... AS SELECT * FROM view.

    Parameters:
        view_name (str): The name of the view to materialize.
        destination_table (str): The name of the table to create/replace.
        dataset_id (str): The BigQuery dataset.
        project_id (str): The GCP project ID.
    """
    client = _get_client(project_id)
    sql = f"""
        CREATE OR REPLACE TABLE `{project_id}.{dataset_id}.{destination_table}` AS
        SELECT * FROM `{project_id}.{dataset_id}.{view_name}`
    """

    start_time = time.time()
    print(f"[bq] Materializing {view_name} → {destination_table} ...")
    query_job = client.query(sql)
    query_job.result(retry=_BQ_RETRY, timeout=300)  # Wait for completion; retries on transient 503s
    elapsed_time = time.time() - start_time

    print(f"[bq] Materialized {view_name} → {destination_table} in {elapsed_time:.2f}s")