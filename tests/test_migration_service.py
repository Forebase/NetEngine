from netengine.utils.migration_service import (
    MigrationApplyError,
    postgres_allows_transaction,
    split_sql_statements,
)


def test_split_sql_statements_preserves_plpgsql_function_body() -> None:
    sql = """
    CREATE FUNCTION demo() RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
        RAISE NOTICE 'semi; inside';
    END;
    $$;
    CREATE TABLE demo_table(id int);
    """

    statements = split_sql_statements(sql)

    assert len(statements) == 2
    assert "RAISE NOTICE 'semi; inside';" in statements[0]
    assert statements[1] == "CREATE TABLE demo_table(id int);"


def test_postgres_allows_transaction_detects_non_transactional_operations() -> None:
    assert postgres_allows_transaction("CREATE TABLE demo(id int);") is True
    assert postgres_allows_transaction("CREATE INDEX CONCURRENTLY idx ON demo(id);") is False
    assert postgres_allows_transaction("VACUUM demo;") is False


def test_migration_apply_error_includes_filename_context_and_database_error() -> None:
    error = MigrationApplyError("002_bad.sql", "CREATE TABLE bad", RuntimeError("boom"))

    message = str(error)

    assert "002_bad.sql" in message
    assert "CREATE TABLE bad" in message
    assert "boom" in message
