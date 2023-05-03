import nox

@nox.session
def lint(session):
    session.install("Flake8-pyproject")
    for package in ['isort', 'black', 'flake8']:
        session.install(package)
        session.run(package, "src")
