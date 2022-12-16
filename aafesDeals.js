#!/usr/local/bin/node
/*
 * <xbar.title>NBA Bling</xbar.title>
 * <xbar.version>v1.0</xbar.version>
 * <xbar.author>hodgesd</xbar.author>
 * <xbar.author.github>hodgesd</xbar.author.github>
 * <xbar.desc>AAFES Deal of the Day Sale</xbar.desc>
 * <xbar.dependencies>node</xbar.dependencies>
 * <xbar.abouturl></xbar.abouturl>
 */

const puppeteer = require('puppeteer');

const url = 'https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=dotd';
console.log('DOTD' + '\n---\n');
(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.goto('https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=dotd');
  //   await page.waitForSelector('.aafes-section-title');

  const getDOTD = await page.evaluate(() => {
    const menuArray = [];
    const salesItems = document.querySelectorAll(
      '.aafes-thumbnail-item.col-xs-12'
    );
    const salesItemsArray = Array.from(salesItems);
    console.log(salesItemsArray.length + ' items on sale today' + '\n---\n');
    salesItemsArray.forEach((salesItem) => {
      const itemName = salesItem
        .querySelector('.aafes-item-name')
        .querySelector('a')
        .textContent.trim();
      const itemSalePrice =
        salesItem
          .querySelector('.item-pricing')
          .querySelector('.aafes-price-sale')
          ?.textContent.trim()
          .slice(-4, -1) || '🔑';
      const itemDiscount =
        salesItem
          .querySelector('.aafes-price-saved')
          ?.textContent.trim()
          .slice(-4, -1) || ''; // get the discount percentage... always 2 digits?
      const itemLink = salesItem.querySelector('a').href;
      menuArray.push(
        `${itemSalePrice} ${itemDiscount} ${itemName} | href=${itemLink}`
      );
    });
    return menuArray;
  });
  getDOTD.forEach((item) => {
    console.log(item);
  });
  await browser.close();
})();
