from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv
import asyncio
import os

load_dotenv()


async def main():
    # If HEADLESS is empty, show browser (headless=False)
    # If HEADLESS has any value, run headless (headless=True)
    headless = bool(os.getenv('HEADLESS', '').strip())

    browser = Browser(
        headless=headless,
        window_size={'width': 1000, 'height': 700},
    )

    llm = ChatOpenAI(model="gpt-4o-mini")
    task = "Find the number 1 post on Show HN"

    agent = Agent(
        task=task,
        browser=browser,
        llm=llm,
    )

    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
