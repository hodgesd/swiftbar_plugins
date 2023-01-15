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

// TODO: filter out PC games
// TODO: sort by rating
// TODO: sort by price
// TODO: add a link to the game

const puppeteer = require('puppeteer');
const url =
  'https://store.steampowered.com/specials/?facets13268=6%3A2&offset=12/';
console.log('🕹️' + '\n---\n');
console.log(`Steam Mac Deals | href= ${url}` + '\n---\n');

(async () => {
  // console.log('test' + '\n---\n');
  const browser = await puppeteer.launch({ headless: true });
  var [page] = await browser.pages();
  await page.goto(
    'https://store.steampowered.com/specials/?facets13268=6%3A2&offset=12'
  );

  // wait for games to load
  await page
    .waitForSelector('.salepreviewwidgets_SaleItemBrowserRow_y9MSd')
    .then(() => {
      // console.log('selector found');
    });

  const getGames = await page.evaluate(() => {
    const gameList = [];
    const gameJSON = [];

    // Find all the elements with the game class
    const games = document.querySelectorAll(
      '.salepreviewwidgets_SaleItemBrowserRow_y9MSd'
    );

    // if (!q

    const gamesArray = Array.from(games);

    // Loop through the games and print game info
    gamesArray.forEach((game) => {
      const gameSalePrice = game
        .querySelector('.salepreviewwidgets_StoreSalePriceBox_Wh0L8')
        ?.textContent?.split('.')[0];
      const gameDiscount = game.querySelector(
        '.salepreviewwidgets_StoreSaleDiscountBox_2fpFv'
      )?.textContent;
      const gameRating = game.querySelector(
        '.gamehover_ReviewScoreValue_2SbZz.gamehover_ReviewScoreHigh_1Emes div'
      )?.textContent;
      const gameTitle = game.querySelector(
        '.salepreviewwidgets_StoreSaleWidgetTitle_3jI46'
      )?.textContent;
      gameList.push(
        `${gameSalePrice} [${gameDiscount}] ${gameTitle} (${gameRating})`
      );
      gameJSON.push({
        gameTitle,
        gameSalePrice,
        gameDiscount,
        gameRating,
      });
    });
    return [gameList, gameJSON];
  });

  const [gameList, gameJSON] = getGames;

  // console.log(gameJSON);

  // const gamesSortedByPrice = gameJSON.sort((a, b) => {
  //   return a.gameSalePrice - b.gameSalePrice;
  // });

  gameJSON.sort((a, b) => {
    if (a.gameDiscount < b.gameDiscount) {
      return 1;
    }
    if (a.gameDiscount > b.gameDiscount) {
      return -1;
    }
    return 0;
  });

  gameJSON.forEach((g) => {
    console.log(
      `${g.gameSalePrice} [${g.gameDiscount}] ${g.gameTitle} (${g.gameRating})`
    );
  });

  // gameList.forEach((item) => {
  //   console.log(item);
  // });
  // console.log('yo');
  // console.log(getGames);
  await browser.close();
})();
