import datetime
import time

import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

# Explicit schema for the balance-history table. Using an explicit schema (rather
# than autodetect) guarantees snapshot_date is a native DATE and pins the column
# types the front end relies on.
BALANCES_HISTORY_SCHEMA = [
    bigquery.SchemaField("snapshot_date", "DATE"),
    bigquery.SchemaField("id", "STRING"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("balance", "FLOAT"),
    bigquery.SchemaField("cleared_balance", "FLOAT"),
    bigquery.SchemaField("uncleared_balance", "FLOAT"),
    bigquery.SchemaField("source", "STRING"),
]


def _column_type(schema, column):
    """Return the BigQuery field type for ``column`` in ``schema``, or None."""
    for field in schema or []:
        if field.name == column:
            return field.field_type
    return None


def _coerce_date_column(df, column, col_type):
    """If the target column is a DATE, convert it to datetime.date objects."""
    if col_type == "DATE":
        df = df.copy()
        df[column] = pd.to_datetime(df[column]).dt.date
    return df


def _date_param_values(values, col_type):
    """Build (param_type, values) for a DATE or STRING query parameter."""
    if col_type == "DATE":
        return "DATE", [datetime.date.fromisoformat(str(v)) for v in values]
    return "STRING", [str(v) for v in values]

def append_to_bigquery(data, table_id, dataset_id='home_ynab', project_id='ecstatic-pod-443723-f6'):
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

    client = bigquery.Client(project=project_id)
    table_ref = client.dataset(dataset_id).table(table_id)

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        autodetect=True
    )

    start_time = time.time()
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()  # Waits for the job to complete
    elapsed_time = time.time() - start_time

    print(f"Appended {len(df)} rows to {dataset_id}.{table_id} in {elapsed_time:.2f} seconds")

def write_to_bigquery(data, table_id, dataset_id='home_ynab', project_id='ecstatic-pod-443723-f6'):
    """
    Writes a pandas DataFrame to an existing BigQuery table.

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

    client = bigquery.Client(project=project_id)
    table_ref = client.dataset(dataset_id).table(table_id)

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        autodetect=True,
    )

    start_time = time.time()
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()  # Waits for the job to complete
    elapsed_time = time.time() - start_time

    print(f"Wrote {len(df)} rows to {dataset_id}.{table_id} in {elapsed_time:.2f} seconds")

def append_daily_snapshot(data, table_id, snapshot_date, snapshot_column='snapshot_date',
                          schema=None, dataset_id='home_ynab', project_id='ecstatic-pod-443723-f6'):
    """
    Appends a dated daily snapshot to a history table.

    Before appending, any rows already present for ``snapshot_date`` are deleted
    so that re-running on the same day replaces that day's rows rather than
    creating duplicates (idempotent per day).

    Parameters:
        data (pd.DataFrame or str): A pandas DataFrame or path to a CSV file.
            Must already contain ``snapshot_column``.
        table_id (str): The ID of the history table in BigQuery.
        snapshot_date (str): The snapshot date (e.g. ``"2026-06-20"``) whose
            rows should be replaced.
        snapshot_column (str): The column holding the snapshot date.
        schema (list[bigquery.SchemaField] | None): Explicit table schema. When
            given, autodetect is disabled and the column types are pinned (this is
            how snapshot_date becomes a native DATE).
        dataset_id (str): The ID of the dataset in BigQuery.
        project_id (str): The GCP project ID.
    """
    if isinstance(data, str):
        df = pd.read_csv(data)
    elif isinstance(data, pd.DataFrame):
        df = data
    else:
        raise ValueError("Input must be a pandas DataFrame or path to a CSV file.")

    snapshot_type = _column_type(schema, snapshot_column)
    df = _coerce_date_column(df, snapshot_column, snapshot_type)

    client = bigquery.Client(project=project_id)
    table_ref = client.dataset(dataset_id).table(table_id)

    # If the table already exists, remove any rows for this snapshot date so a
    # re-run replaces them instead of accumulating duplicates. On the very first
    # run the table doesn't exist yet, so there is nothing to delete.
    try:
        client.get_table(table_ref)
        param_type, param_values = _date_param_values([snapshot_date], snapshot_type)
        delete_query = (
            f"DELETE FROM `{project_id}.{dataset_id}.{table_id}` "
            f"WHERE {snapshot_column} = @snapshot_date"
        )
        delete_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("snapshot_date", param_type, param_values[0])
            ]
        )
        client.query(delete_query, job_config=delete_config).result()
    except NotFound:
        pass

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        schema=schema,
        autodetect=schema is None,
    )

    start_time = time.time()
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()  # Waits for the job to complete
    elapsed_time = time.time() - start_time

    print(f"Appended {len(df)} snapshot rows for {snapshot_date} to "
          f"{dataset_id}.{table_id} in {elapsed_time:.2f} seconds")

def existing_snapshot_dates(table_id, snapshot_column='snapshot_date',
                            dataset_id='home_ynab', project_id='ecstatic-pod-443723-f6'):
    """
    Returns the set of snapshot dates (as strings) already present in a history
    table. Returns an empty set if the table does not exist yet.
    """
    client = bigquery.Client(project=project_id)
    table_ref = client.dataset(dataset_id).table(table_id)
    try:
        client.get_table(table_ref)
    except NotFound:
        return set()
    query = (
        f"SELECT DISTINCT {snapshot_column} AS snapshot_date "
        f"FROM `{project_id}.{dataset_id}.{table_id}`"
    )
    # str() of a DATE comes back as ISO ("YYYY-MM-DD"), matching the date strings
    # the callers compare against.
    return {str(row["snapshot_date"]) for row in client.query(query).result()}

def append_snapshots(data, table_id, snapshot_dates=None, snapshot_column='snapshot_date',
                     schema=None, dataset_id='home_ynab', project_id='ecstatic-pod-443723-f6'):
    """
    Appends rows spanning one or more snapshot dates to a history table.

    Any rows already present for the given ``snapshot_dates`` are deleted first,
    so the operation is idempotent for those dates (a re-run replaces them rather
    than duplicating). This is the multi-date counterpart to
    ``append_daily_snapshot`` and is used by the backfill.

    Parameters:
        data (pd.DataFrame or str): A pandas DataFrame or path to a CSV file.
            Must already contain ``snapshot_column``.
        table_id (str): The ID of the history table in BigQuery.
        snapshot_dates (list[str] | None): The snapshot dates whose rows should be
            replaced. If ``None``, the distinct values found in the data are used.
        snapshot_column (str): The column holding the snapshot date.
        schema (list[bigquery.SchemaField] | None): Explicit table schema. When
            given, autodetect is disabled and the column types are pinned (this is
            how snapshot_date becomes a native DATE).
        dataset_id (str): The ID of the dataset in BigQuery.
        project_id (str): The GCP project ID.
    """
    if isinstance(data, str):
        df = pd.read_csv(data)
    elif isinstance(data, pd.DataFrame):
        df = data
    else:
        raise ValueError("Input must be a pandas DataFrame or path to a CSV file.")

    if snapshot_dates is None:
        snapshot_dates = df[snapshot_column].astype(str).unique().tolist()
    snapshot_dates = [str(d) for d in snapshot_dates]

    snapshot_type = _column_type(schema, snapshot_column)
    df = _coerce_date_column(df, snapshot_column, snapshot_type)

    client = bigquery.Client(project=project_id)
    table_ref = client.dataset(dataset_id).table(table_id)

    # Replace any existing rows for these dates so the backfill is idempotent.
    try:
        client.get_table(table_ref)
        param_type, param_values = _date_param_values(snapshot_dates, snapshot_type)
        delete_query = (
            f"DELETE FROM `{project_id}.{dataset_id}.{table_id}` "
            f"WHERE {snapshot_column} IN UNNEST(@snapshot_dates)"
        )
        delete_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("snapshot_dates", param_type, param_values)
            ]
        )
        client.query(delete_query, job_config=delete_config).result()
    except NotFound:
        pass

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        schema=schema,
        autodetect=schema is None,
    )

    start_time = time.time()
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()  # Waits for the job to complete
    elapsed_time = time.time() - start_time

    print(f"Appended {len(df)} rows across {len(snapshot_dates)} snapshot date(s) "
          f"to {dataset_id}.{table_id} in {elapsed_time:.2f} seconds")
