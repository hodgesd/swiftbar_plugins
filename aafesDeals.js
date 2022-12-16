#!/usr/local/bin/node
/*
 * <xbar.title>AAFES Deals</xbar.title>
 * <xbar.version>v1.0</xbar.version>
 * <xbar.author>hodgesd</xbar.author>
 * <xbar.author.github>hodgesd</xbar.author.github>
 * <xbar.desc>AAFES Deals of the Day</xbar.desc>
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

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.goto('https://www.shopmyexchange.com/savings-center');

  const getCategories = await page.evaluate(() => {
    const menuList = [];
    const categories = document.querySelectorAll('.item-content');
    const categoryArray = Array.from(categories);
    const bannedCategories = [
      'bras',
      'garmin',
      'fujifilm',
      'military pride',
      'blue topaz',
      "burt's bees gift sets",
      'diamond jewelry',
    ];

    menuList.push(
      'Sale Items | href= https://www.shopmyexchange.com/savings-center' +
        '\n---\n'
    );
    function capitalizeFirstLetter(string) {
      return string.charAt(0).toUpperCase() + string.slice(1);
    }
    // Loop through the items and output the item name, sale price, discount, and link
    categoryArray.forEach((category) => {
      const categoryName = category
        .querySelector('a')
        .textContent.trim()
        .replace('select ', '')
        .split('Off ')[1];
      const categoryLink = category.querySelector('a').href;
      // Log the category name and link if it's not in the bannedCategories array
      const categoryMenuItem =
        categoryName && !bannedCategories.includes(categoryName.toLowerCase())
          ? `--${capitalizeFirstLetter(categoryName)}| href= ${categoryLink}`
          : '';
      menuList.push(categoryMenuItem);
    });
    return menuList;
  });
  getCategories.forEach((item) => {
    console.log(item);
  });
  await browser.close();
})();
