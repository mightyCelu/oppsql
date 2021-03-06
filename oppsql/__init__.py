import warnings
import re

import pandas as pd
import sqlalchemy as sqa

from . import model as m


__all__ = ['get_unique_param', 'get_vector', 'model']


def _map_database_value(val):
    """Map values used in the database to the corresponding python data type.

    The strings 'false' and 'true' are mapped to python's True and False. Numbers are parsed to represent as either
    floats or ints. For values with no specific datatype, this function is the identity.

    Parameters
    ----------
    val : string
        A value from the database, as used in e.g. m.runattr.c.attrValue and m.runparam.c.parValue.

    Returns
    -------
    mapped_val : string or bool
        The given value or its mapped version
    """
    if val == 'true':
        return True
    elif val == 'false':
        return False

    try:
        return int(val)
    except ValueError:
        pass

    try:
        return float(val)
    except ValueError:
        pass

    return val


def _map_python_value(val):
    """Map a python data type to the corresponding string used in the database.

    Bools will be mapped to their lowercase string representation.

    Parameters
    ----------
    val : string, int, float or bool
        A value from the database, as used in e.g. m.runattr.c.attrValue and m.runparam.c.parValue.

    Returns
    -------
    mapped_val : string, int or float
        The given value or its mapped version
    """
    if type(val) == bool:
        return str(val).lower()

    return val


def _ignore_decimal_warning():
    regex = (
        r"^Dialect sqlite\+pysqlite does \*not\* support Decimal objects natively\, "
        "and SQLAlchemy must convert from floating point - rounding errors and other "
        "issues may occur\. Please consider storing Decimal numbers as strings or "
        "integers on this platform for lossless storage\.$")
    warnings.filterwarnings('ignore', regex, sqa.exc.SAWarning, r'^sqlalchemy\.sql\.sqltypes$')


def get_iterationvars(engine):
    """Get the iteration variables used in the simulation

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Database connection.

    Returns
    -------
    iterationvars : dict
        Keys and values correspond to the attributes' names and values.
    """
    iterationvar_stmt = sqa.select([m.runattr.c.attrValue]).where(m.runattr.c.attrName == 'iterationvars')

    def iterationvar_values_stmt(var):
        return sqa.select([m.runattr.c.attrValue]).where(m.runattr.c.attrName == var).distinct()

    with engine.connect() as conn:
        iterationvars = ([re.match(r'\$(\w+)=.*', entry).groups(1)[0]
                          for entry in conn.execute(iterationvar_stmt).fetchone()[0].split(', ')])
        return {var: [_map_database_value(entry[0]) for entry in conn.execute(iterationvar_values_stmt(var)).fetchall()]
                for var in iterationvars}




def get_unique_param(con, name, type):
    """Get a param which is unique for the database

    Raises
    ------
    sqlalchemy.MultipleResultsFound
        If the parameter is not unique.
    """
    return type(con.execute(sqa.select([m.runparam.c.parValue])
                               .where(m.runparam.c.parName.like('%{}'.format(name)))
                               .distinct())
                   .scalar())


def get_vector(engine, by, variable, time=False, run=False, module=False,
               filter_=None, aggregate=None, self_descriptive_result=False):
    """Get OMNeT++ result vectors

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Database engine.
    by : str, list of strings, dict mapping strings to None, a string or a list of strings
        The attribute(s) to group results by. Depending on the type, the semantic changes
        Passing a string, list or tuple will group by the given attributes (as present in the runattr table).
        For each attribute, a column is added which contains the corresponding values.
        When given a dict, its keys are interpreted as a list/tuple. The values can be used to filter the results.
        If the value is a list/tuple, only rows containing the specified entries will be returned. If the value is
        a single value, additionally, the corresponding column is dropped.
    variable : string or list or tuple
        The variable(s) to query. If multiple values are given, a additional column containing the variable name is
        present, if only one is given the value column is renamed to the variable name.
    time : bool
        Include the values' timestamps (in seconds).
    module : bool
        Include the emitting module's name.
    filter_ : sqlalchemy expression
        A valid sqlalchemy constraint that is checked agaisnt all returned rows.
    aggregate : sqlalchemy aggregation function
        If given, results are grouped by the attributes given in `by` and the aggregated by the given
        function is applied.
    self_descriptive_result : bool
        Include columns for attribute values and variable names, even if they are singular, i.e., the contain only
        a unique value.

    Returns
    -------
    df : pandas.DataFrame
        DataFrame containing the data. One column per non-singular attribute and value.

    Notes
    -----
    The simulation repetion number can be included by adding 'repetition' to the attributes. If not included,
    the results of all simulation runs will be included without being able to distinguish them.

    Examples
    --------
    For all attribute-values of 'nCars' return all collisions values.
    >>> df = oppsql.get_vector(engine, 'nCars', 'collisions')
    >>> df.head()
      nCars  collisions
    0   160       150.0
    1   160       161.0
    2   160       138.0
    3   160       151.0
    4   160       155.0

    Include the repetition information.
    >>> df.head()
      nCars repetition  collisions
    0   160          4       150.0
    1   160          4       161.0
    2   160          4       138.0
    3   160          4       151.0
    4   160          4       155.0

    Only return results where nCars equals 320.
    >>> df = oppsql.get_vector(engine, {'nCars': 320, 'repetition': None}, 'collisions')
    >>> df.head()
      repetition  collisions
    0          2       484.0
    1          2       589.0
    2          2       585.0
    3          2       536.0
    4          2       570.0

    Compute the average collisions for each repetition of nCars == 320.
    >>> df = oppsql.get_vector(engine, {'nCars': 320, 'repetition': None}, 'collisions', aggregate=sqa.func.avg)
    >>> df.head()
      repetition  collisions
    0          0  314.153409
    1          1  364.458553
    2          2  412.747559
    3          3  467.429722
    4          4  366.877266

    Compute the average of collisions above 300 for each repetition of nCars == 320.
    >>> df = oppsql.get_vector(engine, {'nCars': 320, 'repetition': None}, 'collisions',
                               filter_=oppsql.model.vectordata.c.value > 300, aggregate=sqa.func.avg)
    >>> df.head()
      repetition  collisions
    0          0  552.576250
    1          1  546.687829
    2          2  549.550911
    3          3  553.535197
    4          4  540.700926
    """
    _ignore_decimal_warning()

    def normalize_by(by):
        """Normalize the different 'by' syntaxes to a map of strings to lists of strings"""
        def normalize_filter(filter_):
            valid_types = (int, float, bool, str)
            if filter_ is None:
                return []
            if type(filter_) in valid_types:
                return [filter_]
            if type(filter_) == list and all(type(val) in valid_types for val in filter_):
                return filter_
            else:
                raise TypeError("Filter must be string, int, float or bool, a list of these or None")

        if type(by) == str:
            return {by: []}
        elif type(by) == list and all(type(attr) == str for attr in by):
            return {attr: [] for attr in by}
        elif type(by) == dict and all(type(attr) == str for attr in by):
            return {attr: normalize_filter(filter_) for attr, filter_ in by.items()}
        else:
            raise TypeError("By must be string, list of strings or dictionary")

    def normalize_variable(variable):
        """Normalize the different variable syntaxes to a list of strings"""
        return [variable] if type(variable) == str else variable

    def simtime(simtime_raw, simtime_exponent):
        """Compute float simtime from fixed-point notation"""
        return simtime_raw * 10 ** simtime_exponent

    def single_filter(by, attribute):
        return len(by[attribute]) == 1

    def attribute_filter_expression(by, attribute):
        return sqa.and_(m.runattr.c.attrName == attribute,
                        m.runattr.c.attrValue.in_(_map_python_value(val) for val in by[attribute])
                        if by[attribute] else True)

    # Normalize parameters TODO complete: variables -> list, by -> dict
    by = normalize_by(by)
    variable = normalize_variable(variable)

    attribute_subqueries = {attribute: sqa.select([m.runattr.c.runId, m.runattr.c.dbId, m.runattr.c.attrValue])
                                          .where(attribute_filter_expression(by, attribute))
                                          .alias()
                            for attribute in by}

    select = []
    select.extend(query.c.attrValue.label(attribute)
                  for attribute, query in attribute_subqueries.items()
                  if not (len(by[attribute]) == 1 and not self_descriptive_result))
    if time:
        select.append(sqa.func.simtime(m.vectordata.c.simtimeRaw, m.run.c.simtimeExp).label('simtime'))
    if module:
        select.append(m.vector.c.moduleName)
    if not (len(variable) == 1 and not self_descriptive_result):
        select.append(m.vector.c.vectorName)

    if aggregate is not None:
        select.append(aggregate(m.vectordata.c.value))
    else:
        select.append(m.vectordata.c.value)

    tables = (m.run
               .join(m.vector)
               .join(m.vectordata))
    for query in attribute_subqueries.values():
        tables = tables.join(query)

    constraints = []
    if filter_ is not None:
        constraints.append(filter_)
    constraints.append((m.vector.c.vectorName.in_(variable)))

    stmt = sqa.select(select).select_from(tables).where(sqa.and_(*constraints))
    if aggregate is not None:
        stmt = stmt.group_by(*(query.c.attrValue
                               for attribute, query in attribute_subqueries.items()
                               if not single_filter(by, attribute)))

    with engine.connect() as conn:
        if time:
            conn.connection.connection.create_function('simtime', 2, simtime)

        df = pd.read_sql(stmt, conn)
        for attr, vals in by.items():  # FIXME find out why .assign()-based approach does not work
            df[attr] = df[attr].astype(pd.api.types.CategoricalDtype(vals, ordered=True)
                                       if type(vals[0]) == str else type(vals[0]))

        return df
