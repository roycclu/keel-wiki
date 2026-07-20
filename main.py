from pywikibot import config
config.simulate = False

from workflow import start_task

def main():
    start_task()

if __name__ == "__main__":
    main()