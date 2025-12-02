import os
import sys
import importlib
import inspect
from logging.config import fileConfig

from dotenv import load_dotenv

load_dotenv() # Moved to top

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
# import app.models # <--- REMOVED THIS LINE

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Set the database URL from the environment variables
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
config.set_main_option("sqlalchemy.url", DB_URL)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
from app.db import Base # <--- Keep this import for Base

target_metadata = Base.metadata
# target_metadata = None

# <--- NEW FUNCTION START ---
def import_all_models_from_package(package_name: str):
    """Dynamically imports all Python modules within a given package
    to ensure SQLAlchemy models are registered with Base.metadata."""
    
    # Add the project root to sys.path if not already there
    # This is crucial for relative imports like 'app.db' to work
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        package = importlib.import_module(package_name)
        package_dir = os.path.dirname(package.__file__)
    except ImportError:
        print(f"Warning: Could not import package {package_name}. Skipping model discovery.")
        return

    for root, _, files in os.walk(package_dir):
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                module_path = os.path.join(root, file)
                # Calculate relative path from package_dir to module_path
                rel_path = os.path.relpath(module_path, package_dir)
                # Convert file path to module name (e.g., 'user_model.py' -> 'user_model')
                module_name = os.path.splitext(rel_path)[0].replace(os.sep, '.')
                full_module_name = f"{package_name}.{module_name}"
                
                try:
                    module = importlib.import_module(full_module_name)
                    # Optional: inspect module to ensure models inheriting from Base are loaded
                    # This step is mostly for verification; importing the module is usually enough
                    # for Base.metadata to pick up the models if they are defined correctly.
                    for name, obj in inspect.getmembers(module):
                        if inspect.isclass(obj) and issubclass(obj, Base) and obj != Base:
                            # Model found and registered with Base.metadata
                            pass
                except Exception as e:
                    print(f"Warning: Could not import module {full_module_name}: {e}")

# Call the function to import all models
import_all_models_from_package('app.models') # <--- NEW CALL
# <--- NEW FUNCTION END ---

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
