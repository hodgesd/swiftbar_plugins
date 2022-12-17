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

// # <swiftbar.hideAbout>true</swiftbar.hideAbout>
// # <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
// # <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
// # <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
// # <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

const puppeteer = require('puppeteer');

console.log('BX' + '\n---\n'); // BX is the title of the menu bar item

async function getSaleCategories() {
  const SALES_URL = 'https://www.shopmyexchange.com/savings-center';
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
      `Sale Categories | href= https://www.shopmyexchange.com/savings-center` +
        '\n---\n'
    );
    function capitalizeFirstLetter(string) {
      return string.charAt(0).toUpperCase() + string.slice(1);
    }
    // Loop through the items and output the item name, sale price, discount, and link

    async function getSalesItemsFromSalesCategories(url) {
      // const browser = await puppeteer.launch({ headless: false });
      const cat_page = await browser.newPage();
      // console.log(url);
      await cat_page.goto(url);

      const getItems = await cat_page.evaluate(() => {
        const categorySalesItems = [];
        const categoryItems = [
          ...document.querySelectorAll('.item-tag.save'),
        ].map((e) => e.parentNode);

        // Loop through the items and output the item name, sale price, discount, and link
        categoryItems.forEach((catItem) => {
          const itemName = catItem
            .querySelector('.aafes-item-name')
            .querySelector('a')
            .textContent.trim();
          const itemLink = catItem.querySelector('a').href;
          const itemPrice =
            catItem
              .querySelector('.item-pricing')
              .querySelector('.aafes-price-sale')
              ?.textContent.trim()
              .split('.')[0] || '🔑';
          const itemDiscount =
            catItem
              .querySelector('.aafes-price-saved')
              ?.textContent.trim()
              .slice(-4, -1) || '';
          const itemMenuItem = `----${itemPrice} [-${itemDiscount}]${itemName} | href= ${itemLink} length= 90`;
          debugger;

          categorySalesItems.push(itemMenuItem);
        });
        // console.log(categorySalesItems);
        return categorySalesItems;
      });
      // await cat_page.close();
      return getItems;
    }
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
      const categorySumMenuItems =
        getSalesItemsFromSalesCategories(categoryLink);

      menuList.push(categoryMenuItem);
      menuList.push(categorySumMenuItems);
    });
    return menuList;
  });
  getCategories.forEach((item) => {
    console.log(item);
  });
  await browser.close();
}

const DOTD_URL = 'https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=dotd';

async function getDOTD() {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.goto(DOTD_URL);
  //   await page.waitForSelector('.aafes-section-title');

  const getDOTD = await page.evaluate(() => {
    const menuArray = [];
    const salesItems = document.querySelectorAll(
      '.aafes-thumbnail-item.col-xs-12'
    );

    if (!salesItems.length) {
      // if .aafes-page-head.mb-0 exists, use that, otherwise 'No sales items found today'
      const itemName = document.querySelector('.aafes-page-head.mb-0')
        ? document.querySelector('.aafes-page-head.mb-0').textContent.trim()
        : 'No sales items found today';
      const itemLink = document.querySelector('.aafes-page-head.mb-0')
        ? document.querySelector('.aafes-page-head.mb-0').href
        : '';
      menuArray.push(itemName + ' | href=' + itemLink);
    }

    const salesItemsArray = Array.from(salesItems);
    menuArray.push(salesItemsArray.length + ' items on sale today' + '\n---\n');
    salesItemsArray.forEach((salesItem) => {
      // const itemName =
      //   salesItem
      //     .querySelector('.aafes-item-name')
      //     ?.querySelector('a')
      //     .textContent.trim() ||
      //   salesItem
      //     .querySelector('.title aafes-page-head.mb-0')
      //     .textContent.trim();
      // if salesItem.querySelector('.aafes-item-name') exists, use that, otherwise use .title aafes-page-head.mb-0
      const itemName = salesItem
        .querySelector('.aafes-item-name')
        .querySelector('a')
        .textContent.trim();
      // menuArray.push(itemName);
      const itemSalePrice =
        salesItem
          .querySelector('.item-pricing')
          .querySelector('.aafes-price-sale')
          ?.textContent.trim()
          .split('.')[0] || '🔑';
      const itemDiscount =
        salesItem
          .querySelector('.aafes-price-saved')
          ?.textContent.trim()
          .slice(-4, -1) || ''; // get the discount percentage... always 2 digits?
      const itemLink = salesItem.querySelector('a').href;

      menuArray.push(
        `${itemSalePrice} [-${itemDiscount}] ${itemName} | href=${itemLink}`
      );
    });
    return menuArray;
  });
  getDOTD.forEach((item) => {
    console.log(item);
  });
  await browser.close();
}

getSaleCategories();
getDOTD();
