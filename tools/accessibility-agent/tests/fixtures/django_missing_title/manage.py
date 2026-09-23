# Minimal manage.py so FrameworkDetector identifies this as a Django project
import sys

if __name__ == "__main__":
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
