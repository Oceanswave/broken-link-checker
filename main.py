import asyncio
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from urllib.parse import urljoin, urlparse
from collections import deque
import csv
import re
import os

# Load environment variables
load_dotenv()

# Constants
START_URL = os.getenv('START_URL')
LOGIN_URL = os.getenv('LOGIN_URL')
DOMAIN = urlparse(START_URL).netloc

# Initialize deque with the starting URL
queue = deque([(None, START_URL)])
visited = {}
skipped = set()
broken_links = set()
exclude_patterns = [
    r'^.*?/login',
    r'^.*?/logout',
    #r'https://mycommittees-dev.api.org',
]

async def get_page_links(page, parent_url, url):
    try:
        print(f'Visiting {url}')
        start_time = asyncio.get_running_loop().time()
        response = await page.goto(url)
        
        # wait for page to load
        await page.wait_for_load_state('networkidle')
        end_time = asyncio.get_running_loop().time()
        load_time = end_time - start_time  # Calculate load time
        print(f'Page loaded in {load_time:.2f} seconds.')

        if response.status == 404:
            broken_links.add((parent_url, url, load_time))
            return [], load_time
        # Extract all links on the page
        return await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a')).map(a => a.href);
        }"""), load_time
    except Exception as e:
        end_time = asyncio.get_running_loop().time()
        load_time = end_time - start_time
        print(f'Error loading page: {e}')
        broken_links.add((parent_url, url, load_time))
        return [], load_time

async def worker(browser):
    while queue:
        parent_url, current_url = queue.popleft()
        if current_url in visited:
            continue

        visited[current_url] = {
            'links': 0,
            'load_time': 0
        }

        # Skip external links
        if DOMAIN not in current_url:
            print(f'Skipping {current_url}: URL is external')
            skipped.add((parent_url, current_url, "External URL"))
            continue
        # Skip regex patterns contained in the exclude list
        if current_url != START_URL and any(re.search(pattern, current_url) for pattern in exclude_patterns):
            print(f'Skipping {current_url}: URL matches exclude pattern')
            skipped.add((parent_url, current_url, "URL matches exclude pattern"))
            continue
        
        # Start a new page to check the links
        page = await browser.new_page()
        links, load_time = await get_page_links(page, parent_url, current_url)
        visited[current_url] = { 
            'links': len(links),
            'load_time': load_time
        }

        await page.close()
        # Add new links to the queue
        for link in links:
            absolute_link = urljoin(current_url, link)
            if absolute_link not in visited:
                queue.append((current_url, absolute_link))

async def login(browser):
    # Navigate to the login url
    page = await browser.new_page()
    await page.goto(LOGIN_URL)
    # Wait for the username field to load
    await page.click('#i0116')
    # Fill in the username
    await page.fill('#i0116', os.getenv('LOGIN_EMAIL'))
    # Click the next button
    await page.click('#idSIButton9')
    # Wait for the password field to load
    await page.wait_for_selector('#i0118')
    # Fill in the password
    await page.fill('#i0118', os.getenv('LOGIN_PASSWORD'))
    # Click the sign in button
    await page.click('#idSIButton9')
    # Wait for the page to load
    await page.wait_for_load_state('networkidle')
    # Click the yes button to stay signed in
    await page.click('#idSIButton9')
    # Wait for the page to load
    await page.wait_for_load_state('networkidle')
    # Close the login page
    await page.close()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        await login(context)

        workers = [asyncio.create_task(worker(context)) for _ in range(5)]  # 5 parallel browsers
        await asyncio.gather(*workers)
        await browser.close()

    # Output the visited links
    print('Visited links:')
    for url, data in visited.items():
        if (data['load_time'] <= 0):
            continue
        print(f'Visited: {url} - Links: {data['links']} Load Time: ({data['load_time']:.2f})')
    
    # Output visited URLs and their load times to a CSV
    with open('visited_links.csv', 'w', newline='', encoding='utf-8') as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['URL', 'Links', 'Load Time (seconds)'])
        for url, data in visited.items():
            if (data['load_time'] <= 0):
                continue
            csv_writer.writerow([url, f"{data['links']}", f"{data['load_time']:.2f}"])

    # Output the broken links
    print('Broken links:')
    for parent, link, load_time in broken_links:
        print(f'Parent page: {parent} - Broken link: {link} Load Time: ({load_time:.2f})')

    # output the broken links to a csv
    with open('broken_links.csv', 'w', newline='', encoding='utf-8') as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['Parent page', 'Broken link', 'Load time (seconds)'])
        for parent, link, load_time in broken_links:
            csv_writer.writerow([parent, link, f"{load_time:.2f}"])

    # Output the skipped links
    print('Skipped links:')
    for parent, link, reason in skipped:
        print(f'Parent page: {parent} - Skipped link: {link} Reason: {reason}')

    # output the skipped links to a csv
    with open('skipped_links.csv', 'w', newline='', encoding='utf-8') as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['Parent page', 'Skipped link', 'Skipped Reason'])
        for parent, link, reason in skipped:
            csv_writer.writerow([parent, link, f'{reason}'])

if __name__ == "__main__":
    asyncio.run(main())