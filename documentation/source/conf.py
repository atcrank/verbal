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

# -- Auto-generate stubs for external RST files ------------------------------
import glob

# Get the directory where conf.py lives (documentation/source)
source_dir = os.path.dirname(os.path.abspath(__file__))

# The real files live in verbal/metacognition/metacognition_trials
trials_source_dir = os.path.abspath(os.path.join(source_dir, '../../metacognition/metacognition_trials'))

# We want to create stubs in documentation/source/metacognition_trials
trials_dest_dir = os.path.join(source_dir, 'metacognition_trials')
os.makedirs(trials_dest_dir, exist_ok=True)

for trial_file in glob.glob(os.path.join(trials_source_dir, '*.rst')):
    filename = os.path.basename(trial_file)
    stub_path = os.path.join(trials_dest_dir, filename)
    with open(stub_path, 'w', encoding='utf-8') as f:
        f.write(f".. include:: ../../../metacognition/metacognition_trials/{filename}\n")
