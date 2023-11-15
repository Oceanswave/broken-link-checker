import asyncio
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page
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
queue = deque([(None, START_URL, 'anchor')])
visited = {}
skipped = set()
visited_images = {}
broken_links = set()
broken_images = set()
exclude_patterns = [
    r'^.*?/login',
    r'^.*?/logout',
]

async def generate_good_url(url):
    # Replace the relative path by appending myc- and change the slashes to dashes
    good_url = re.sub(r'^.*?/', 'myc-', url)
    # Replace -default.aspx with nada
    good_url = re.sub('.*/default.aspx$', '', good_url)
    # Replace all links that end with .aspx with a link to a service with with the query
    good_url = re.sub(r'(.*)\.aspx$', r'\1.svc?wsdl', good_url)

async def get_page_links(page: Page, parent_url, url):
    try:
        print(f'Visiting {url}')
        start_time = asyncio.get_running_loop().time()
        response = await page.goto(url)
        
        # wait for page to load
        await page.wait_for_load_state('networkidle')
        end_time = asyncio.get_running_loop().time()
        load_time = end_time - start_time  # Calculate load time
        print(f'Page loaded in {load_time:.2f} seconds.')

        if response.status == 404 or not response.ok:
            broken_links.add((parent_url, url, load_time))
            return [], [], load_time
        
        # Extract all links on the page
        image_links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('img')).map(img => img.src);
        }""")

        anchor_links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a')).map(a => a.href);
        }""")

        # return the joined list of links
        return image_links, anchor_links, load_time
    except Exception as e:
        end_time = asyncio.get_running_loop().time()
        load_time = end_time - start_time
        print(f'Error loading page: {e}')
        broken_links.add((parent_url, url, load_time))
        return [], [], load_time
    
async def validate_image_link(page: Page, parent_url: str, img_url: str):
    try:
        print(f'Checking image: {img_url}')
        start_time = asyncio.get_running_loop().time()
        response = await page.goto(img_url)
        
        # wait for page to load
        await page.wait_for_load_state('networkidle')
        end_time = asyncio.get_running_loop().time()
        load_time = end_time - start_time  # Calculate load time
        print(f'Image loaded in {load_time:.2f} seconds.')

        if response.status == 404 or not response.ok:
            broken_images.add((parent_url, img_url, load_time))

    except Exception as e:
        end_time = asyncio.get_running_loop().time()
        load_time = end_time - start_time
        print(f'Error loading image: {e}')
        broken_images.add((parent_url, img_url, load_time))

    return load_time


async def worker(browser):
    while queue:
        parent_url, current_url, link_type = queue.popleft()
        if current_url in visited:
            continue

        if current_url in visited_images:
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

        anchor_links = []
        image_links = []
        match link_type:
            case "anchor":
                image_links, anchor_links, load_time = await get_page_links(page, parent_url, current_url)
                visited[current_url] = { 
                    'anchor_links': len(anchor_links),
                    'image_links': len(image_links),
                    'load_time': load_time
                }
            case "image":
                load_time = await validate_image_link(page, parent_url, current_url)
                visited_images[current_url] = {
                    'load_time': load_time
                }
            case _:
                raise Exception(f'Unknown link type: {link_type}')

        await page.close()
        # Add new links to the queue
        for link in anchor_links:
            absolute_link = urljoin(current_url, link)
            if absolute_link not in visited:
                queue.append((current_url, absolute_link, 'anchor'))

        for link in image_links:
            absolute_link = urljoin(current_url, link)
            if absolute_link not in visited:
                queue.append((current_url, absolute_link, 'image'))

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
        print(f'Visited: {url} - Images: {data['image_links']} Links: {data['anchor_links']} Load Time: ({data['load_time']:.2f})')
    
    # Output visited URLs and their load times to a CSV
    with open('visited_links.csv', 'w', newline='', encoding='utf-8') as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['URL', 'Images', 'Links', 'Load Time (seconds)'])
        for url, data in visited.items():
            if (data['load_time'] <= 0):
                continue
            csv_writer.writerow([url, f"{data['image_links']}", f"{data['anchor_links']}", f"{data['load_time']:.2f}"])

    # Output the visited images
    print('Visited images:')
    for url, data in visited_images.items():
        if (data['load_time'] <= 0):
            continue
        print(f'Visited Image: {url} - Load Time: ({data['load_time']:.2f})')
    
    # Output visited image URLs and their load times to a CSV
    with open('visited_images.csv', 'w', newline='', encoding='utf-8') as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['URL', 'Load Time (seconds)'])
        for url, data in visited_images.items():
            if (data['load_time'] <= 0):
                continue
            csv_writer.writerow([url, f"{data['load_time']:.2f}"])

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

    # Output the broken images
    print('Broken images:')
    for parent, link, load_time in broken_images:
        print(f'Parent page: {parent} - Broken image: {link} Load Time: ({load_time:.2f})')

    # output the broken images to a csv
    with open('broken_images.csv', 'w', newline='', encoding='utf-8') as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['Parent page', 'Broken Image Url', 'Load time (seconds)'])
        for parent, image_link, load_time in broken_images:
            csv_writer.writerow([parent, image_link, f"{load_time:.2f}"])

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