## Setup

Clone repository from git. Afterward, run the following to execute this script in a virtual environment. 

`python -m venv venv`

`venv/Scripts/activate`

`pip install -r requirements.txt`

`pip install -e .`

To run the unit tests all at once from the terminal:

`venv/Scripts/activate`

`pytest tests/ -v`