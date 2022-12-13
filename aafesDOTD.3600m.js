#!/usr/local/bin/node
/*
 * <xbar.title>jsSwiftBar</xbar.title>
 * <xbar.version>v1.0</xbar.version>
 * <xbar.author>hodgesd</xbar.author>
 * <xbar.author.github>hodgesd</xbar.author.github>
 * <xbar.desc>AAFES Deal of the Day Sale</xbar.desc>
 * <xbar.dependencies>node</xbar.dependencies>
 * <xbar.abouturl></xbar.abouturl>
 */

const jsdom = require('jsdom');
const { JSDOM } = jsdom;

// Get the current date in the format DMonthName
// e.g. 13December
// todo: add leading zero to day if day < 10?
const date = new Date();
let day = date.getDate();
let monthName = date.toLocaleString('default', { month: 'long' });

// console.log(`${day}${monthName}`);

url = `https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=DealoftheDay${day}${monthName}`;
// url = 'https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=DealoftheDay13December';

dealsURL = `https://www.shopmyexchange.com/savings-center`;

// Function to get the DOM of a webpage
async function getDOM(url) {
  return await JSDOM.fromURL(url);
}

console.log('BX' + '\n---\n');

function capitalizeFirstLetter(string) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

getDOM(dealsURL).then((dom) => {
  const items = dom.window.document.querySelectorAll('.item-content');
  const itemsArray = Array.from(items);
  console.log(
    'Sale Items | href= https://www.shopmyexchange.com/savings-center' +
      '\n---\n'
  );
  // Loop through the items and output the item name, sale price, discount, and link
  itemsArray.forEach((item) => {
    const categoryName = item
      .querySelector('a')
      .textContent.trim()
      .replace('select ', '')
      .split('Off ')[1];
    const categoryLink = item.querySelector('a').href;
    const categoryMenuItem = categoryName
      ? `--${capitalizeFirstLetter(categoryName)}| href= ${categoryLink}`
      : '';
    console.log(categoryMenuItem);
  });
});

getDOM(url).then((dom) => {
  // Find all DOTD items on sale
  const items = dom.window.document.querySelectorAll('.aafes-thumbnail-item');
  const itemsArray = Array.from(items);
  console.log(
    'Deals of the Day | href= https://www.shopmyexchange.com/s?Ntt=13DecemberDealoftheDay' +
      '\n---\n'
  );
  // Loop through the items and output the item name, sale price, discount, and link
  itemsArray.forEach((item) => {
    const itemName = item
      .querySelector('.aafes-item-name')
      .querySelector('a')
      .textContent.trim();
    const itemSalePrice = item
      .querySelector('.item-pricing')
      .querySelector('.aafes-price-sale')
      .textContent.trim()
      .slice(0, -5); // remove the "Sale" from the end of the price
    const itemDiscount = item
      .querySelector('.aafes-price-saved')
      .textContent.trim()
      .slice(-4, -1); // get the discount percentage... always 2 digits?
    const itemLink = item.querySelector('a').href;
    console.log(
      `${itemSalePrice}[-${itemDiscount}] ${itemName} | href= ${itemLink} length= 40`
    );
  });
});
