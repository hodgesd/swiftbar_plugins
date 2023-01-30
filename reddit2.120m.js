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
  const browser = await puppeteer.launch({ slowMo: 250 });
  var [page] = await browser.pages();
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto('https://www.reddit.com/r/homelab/top/?t=day');
  // await page.waitForSelector('.rpBJOHq2PR60pnwJlUyP0');
  // const lastClassB = await page.waitForSelector(
  //   '.rpBJOHq2PR60pnwJlUyP0 ._eYtD2XCVieq6emjKBH3m:last-child'
  // );

  const lastClassB = await page.$(
    '.rpBJOHq2PR60pnwJlUyP0 ._eYtD2XCVieq6emjKBH3m:last-child:last-child'
  );
  await lastClassB.evaluate((node) => node.scrollIntoView());

  const getPosts = await page.evaluate(() => {
    const postJSON = [];
    const posts = document.querySelectorAll(
      '.rpBJOHq2PR60pnwJlUyP0 ._eYtD2XCVieq6emjKBH3m'
    );
    postJSON;
    const postsArray = Array.from(posts);

    // Loop through the games and print game info
    postsArray.forEach((game) => {
      const postTitle = game?.textContent;
      const postLink = game.closest('a')?.href;
      // const post = game.closest(
      //   '._1oQyIsiPHYt6nx7VOmd1sz.bE7JgM2ex7W3aF3zci5bm.D3IyhBGwXo9jPwz-Ka0Ve'
      // );

      const postCategory = game?.closest('div a span')?.textContent;

      postJSON.push({
        postTitle,
        postLink,
        postCategory,
      });
    });
    return postJSON;
  });

  getPosts.forEach((g) => {
    console.log(`${g.postTitle}[${g.postCategory}] | href=${g.postLink}`);
  });
  await browser.close();
})();
