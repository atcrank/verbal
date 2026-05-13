# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
import django

# 1. Add the Django project root to the sys.path
sys.path.insert(0, os.path.abspath('../..'))

# 2. Setup Django so autodoc can inspect models and services
os.environ['DJANGO_SETTINGS_MODULE'] = 'verbal_config.settings'
django.setup()

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'Verbal'
copyright = '2026, Andrew Cruickshank'
author = 'Andrew Cruickshank'
release = '0.1'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',      # Generates docs from docstrings
    'sphinx.ext.viewcode',     # Adds [source] links to jump to raw code
    'sphinx.ext.napoleon',     # Parses Google/NumPy-style docstrings nicely
]

templates_path = ['_templates']
exclude_patterns = []



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
