"""
SQL query templates loaded from external SQL files.

This module loads SQL templates from the ``sql/`` directory and formats them
using uppercase constants exported by :mod:`data_ingestion.data_ingestion.constants`.

Notes
-----
The SQL templates are plain text files containing placeholder names that are
substituted via ``str.format`` using the values of uppercase constants from
``constants.py``. This keeps SQL separate from Python code for readability
and easier editing.
"""

from importlib.resources import files

from . import constants as cons


def _cons_map() -> dict:
    """
    Build a mapping of uppercase constant names to their values.

    The returned mapping is intended for use with ``str.format`` on SQL
    templates stored in the ``sql/`` directory. Only attributes on
    :mod:`data_ingestion.data_ingestion.constants` whose names are all
    uppercase are included.

    Returns
    -------
    dict
        Mapping from uppercase constant name (str) to its value (Any).
    """

    return {k: v for k, v in cons.__dict__.items() if k.isupper()}


def _load_and_format(filename: str) -> str:
    """
    Load a SQL template file and format it with constants.

    Parameters
    ----------
    filename : str
        The name of the SQL template file (relative to the package ``sql/``
        directory) to load and format.

    Returns
    -------
    str
        The SQL string with placeholders substituted using uppercase
        constants from :mod:`data_ingestion.data_ingestion.constants`.

    Raises
    ------
    FileNotFoundError
        If the specified template file does not exist in the ``sql/``
        directory.
    """

    txt = files(__package__).joinpath("sql", filename).read_text()
    return txt.format(**_cons_map())


VEHICLE_UPDATES_CREATE_TABLE_QUERY = _load_and_format("vehicle_updates_create.sql")
VEHICLE_UPDATES_INSERT_QUERY = _load_and_format("vehicle_updates_insert.sql")

TRIP_UPDATES_CREATE_TABLE_QUERY = _load_and_format("trip_updates_create.sql")
TRIP_UPDATES_INSERT_QUERY = _load_and_format("trip_updates_insert.sql")
