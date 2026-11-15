// SPDX-License-Identifier: GPL-3.0-only
// Gateway-owned resolution targets; protected frontend receipts remain unchanged.
function displaySizingSource(source) {
 const edits=[
  ['[832, 720, 640, 544, 480]','[720, 900, 1080, 600, 480]',2],
  ['Math.min(hardCap, area.width / unitWidth, area.height / unitHeight,','Math.min(hardCap,',1],
  ['Math.min(dynamic ? 1 : Infinity, area.width / source.width, area.height / source.height,','Math.min(dynamic ? 1 : Infinity,',1],
  ['navigationFloor ? Infinity : Math.floor(area.width / 2) * 2','Infinity',1],
  ['navigationFloor ? Infinity : Math.floor(area.height / 2) * 2','Infinity',1],
 ];
 for(const [before,after,count] of edits){if(source.split(before).length-1!==count)throw Error('Unsupported display sizing source');source=source.split(before).join(after);}
 return source;
}
module.exports={displaySizingSource};
