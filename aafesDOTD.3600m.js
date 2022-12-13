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

// url = 'https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=dotd';
url = 'https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=DealoftheDay13December';

// Function to get the DOM of a webpage
async function getDOM(url) {
  return await JSDOM.fromURL(url);
}

console.log('BX' + '\n---\n');

console.log(
  'The Exchange | href= https://www.shopmyexchange.com/s?Ntt=13DecemberDealoftheDay' +
    '\n---\n'
);

getDOM(url).then((dom) => {
  // Find all the elements with the class "clus"
  const items = dom.window.document.querySelectorAll('.aafes-thumbnail-item');
  const itemsArray = Array.from(items);
  // Loop through the stories and output the headline
  itemsArray.forEach((item) => {
    const itemName = item
      .querySelector('.aafes-item-name')
      .querySelector('a')
      .textContent.trim();
    // console.log(itemName);

    const itemSalePrice = item
      .querySelector('.item-pricing')
      .querySelector('.aafes-price-sale')
      .textContent.trim()
      .slice(0, -5);
    const itemDiscount = item
      .querySelector('.aafes-price-saved')
      .textContent.trim()
      .slice(-4, -1);
    const itemLink = item.querySelector('a').href;
    console.log(
      `${itemSalePrice}[-${itemDiscount}] ${itemName} | href= ${itemLink} length= 80`
    );
  });
});
