"""
Capture HTML Mermaid flowchart as PNG/PDF using Playwright
"""
import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def capture_flowchart():
    html_path = Path("d:/smart_cliping/docs/pipeline_flowchart.html").absolute()
    output_dir = Path("d:/smart_cliping/docs")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        await page.goto(f"file:///{html_path.as_posix()}")
        
        await page.wait_for_selector(".mermaid", timeout=30000)
        await asyncio.sleep(3)
        
        await page.pdf(
            path=str(output_dir / "pipeline_flowchart.pdf"),
            format="A4",
            landscape=True,
            print_background=True
        )
        
        mermaid_elements = await page.query_selector_all(".mermaid")
        if mermaid_elements:
            for i, element in enumerate(mermaid_elements):
                box = await element.bounding_box()
                if box:
                    await page.screenshot(
                        path=str(output_dir / f"pipeline_flowchart_{i+1}.png"),
                        clip=box,
                        full_page=False
                    )
        
        await page.screenshot(
            path=str(output_dir / "pipeline_flowchart_full.png"),
            full_page=True
        )
        
        await browser.close()
        
        print(f"PDF saved to: {output_dir}/pipeline_flowchart.pdf")
        print(f"Full PNG saved to: {output_dir}/pipeline_flowchart_full.png")

if __name__ == "__main__":
    asyncio.run(capture_flowchart())
