from workflow import start_task
from pywikibot import config

config.simulate = False


def main():
    start_task()


if __name__ == "__main__":
    main()
