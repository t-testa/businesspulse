"""Versioned, explicitly-defined KPI library; never execute user-supplied expressions."""

KPI_DEFINITIONS = {
    'revenue': {'label': 'Revenue', 'description': 'Sum of revenue for imported completed sales'},
    'gross_profit': {'label': 'Gross profit', 'description': 'Revenue minus direct cost'},
    'margin': {'label': 'Gross margin', 'description': 'Gross profit / revenue'},
    'transactions': {'label': 'Transactions', 'description': 'Number of unique completed sale IDs'},
    'aov': {'label': 'Average transaction value', 'description': 'Revenue / transactions'},
    'customers': {'label': 'Purchasing customers', 'description': 'Unique customer IDs with completed sales'},
}


def default_kpis():
    return list(KPI_DEFINITIONS.keys())
