#!/usr/local/bin/node
/*
 * <xbar.title>steamDeals</xbar.title>
 * <xbar.version>v1.0</xbar.version>
 * <xbar.author>hodgesd</xbar.author>
 * <xbar.author.github>hodgesd</xbar.author.github>
 * <xbar.desc>Mac games on sale at steam.com</xbar.desc>
 * <xbar.dependencies>node</xbar.dependencies>
 * <xbar.abouturl>http://varunmalhotra.xyz/blog/2016/02/bitbar-plugins-for-github-and-producthunt.html</xbar.abouturl>
 */

// # <swiftbar.hideAbout>true</swiftbar.hideAbout>
// # <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
// # <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
// # <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
// # <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

// TODO: add game genre
// TODO: get multiple pages of results

const puppeteer = require('puppeteer');
const URL = 'https://www.reddit.com/r/homelab/top/?t=day';

console.log('r/' + '\n---\n');
console.log(`r/Homelab | href= ${URL}` + '\n---\n');

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
  });
  var [page] = await browser.pages();
  await page.setViewport({ width: 1440, height: 900 });

  // await page.goto('https://www.reddit.com/r/homelab/top/?t=day', {
  //   waitUntil: 'networkidle0',
  // });

  await Promise.all([
    page.goto('https://www.reddit.com/r/homelab/top/?t=day', {
      waitUntil: 'domcontentloaded',
    }),
    page.waitForNetworkIdle({ idleTime: 2000 }),
  ]);
  const getPosts = await page.evaluate(() => {
    const postJSON = [];

    const posts = document.querySelectorAll(
      '.rpBJOHq2PR60pnwJlUyP0 ._1oQyIsiPHYt6nx7VOmd1sz.bE7JgM2ex7W3aF3zci5bm.D3IyhBGwXo9jPwz-Ka0Ve'
    );
    postJSON;
    const postsArray = Array.from(posts);
    // console.log(`postsArray.length: ${postsArray.length}`);

    // Loop through the games and print game info
    postsArray.forEach((game) => {
      const postTitle = game.querySelector('div a div h3')?.textContent;
      const postCategory = game.querySelector('div a span')?.textContent;
      const postLink = game.querySelector(
        'div.y8HYJ-y_lTUHkQIc1mdCq._2INHSNB8V5eaWp4P0rY_mE a'
      )?.href;

      postJSON.push({
        postTitle,
        postCategory,
        postLink,
      });
    });
    return postJSON;
  });

  getPosts.forEach((g) => {
    console.log(`${g.postTitle}[${g.postCategory}] | href=${g.postLink}`);
  });
  await browser.close();
})();
