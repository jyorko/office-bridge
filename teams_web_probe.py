"""Run the bounded Teams web status probe inside its RCC environment."""
from teams_web_backend import status

if __name__ == "__main__":
    print(status())
