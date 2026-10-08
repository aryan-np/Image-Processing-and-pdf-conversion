install:
	pip install -r requirements.txt
migrate:
	python manage.py migrate
run:
	python manage.py runserver 0.0.0.0:8000
test:
	GEN_BACKEND=sync python -m pytest tests/ -v
loadtest-small:
	GEN_BACKEND=sync python manage.py loadtest --slots 11 --runs 1 --ocr off
sweep:
	GEN_BACKEND=sync python manage.py loadtest --sweep 11,25,50 --runs 1 --ocr off
