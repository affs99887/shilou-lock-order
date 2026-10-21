// Read-only, fresh browser context. No cart/login actions and no evasion.
import {chromium} from 'playwright';
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext();
 const page=await context.newPage();
 const response=await page.goto('https://www.apple.com/sg/shop/buy-iphone/iphone-17',{waitUntil:'domcontentloaded',timeout:45000});
 console.log(JSON.stringify({status:response.status(),title:await page.title()}));
 if([403,429,541].includes(response.status()))throw Error('Restricted; stopping all public checks');
 await page.waitForTimeout(3000);
 const controls=await page.locator('main button,main input,main select').evaluateAll(rows=>rows.filter(e=>e.getClientRects().length).map(e=>({tag:e.tagName,type:e.type,name:e.name,id:e.id,value:e.value,autom:e.getAttribute('data-autom'),label:e.getAttribute('aria-label')||e.textContent?.trim().slice(0,100)})));
 console.log(JSON.stringify(controls));
}finally{await browser.close();}
